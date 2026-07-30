import Combine
import Foundation
import JoeOSCore
import UIKit

struct DeviceEnrollmentContext: Equatable, Sendable {
    let serverID: UUID
    let serverName: String
    let audienceOrigin: String
    let profileName: String

    init?(
        contract: ValidatedBootstrapContract,
        endpoint: ValidatedEndpoint,
        profileName: String
    ) {
        guard contract.supportsLocalConsolePairing,
              !contract.hasApplicationAuthentication,
              !contract.hasRoleBasedAccess,
              !contract.hasPrivilegedActions
        else {
            return nil
        }
        let origin = endpoint.origin
        let renderedHost = origin.host.contains(":") ? "[\(origin.host)]" : origin.host
        let defaultPort = origin.scheme == "https" ? 443 : 80
        let rawOrigin = "\(origin.scheme)://\(renderedHost)" +
            (origin.port == defaultPort ? "" : ":\(origin.port)")
        guard let canonical = try? EnrollmentAudienceOrigin(rawOrigin),
              canonical.value == rawOrigin
        else {
            return nil
        }
        serverID = contract.observedServerID
        serverName = contract.displayName
        audienceOrigin = canonical.value
        self.profileName = profileName
    }

    var identity: String {
        "\(serverID.uuidString.lowercased())|\(audienceOrigin)"
    }
}

enum DeviceEnrollmentUIPhase: Equatable {
    case unavailable
    case loadingLocalState
    case ready
    case preparing
    case review
    case signing
    case completing
    case pending
    case paired
    case pairedAtAnotherOrigin
    case cancelled
    case failed
}

@MainActor
final class DeviceEnrollmentCoordinator: ObservableObject {
    @Published private(set) var phase: DeviceEnrollmentUIPhase = .unavailable
    @Published private(set) var context: DeviceEnrollmentContext?
    @Published private(set) var review: DeviceEnrollmentReview?
    @Published private(set) var receipt: StoredDeviceEnrollmentReceipt?
    @Published private(set) var message: String?
    @Published private(set) var hasStoredCompletionJournal = false
    @Published private(set) var hasInvalidCompletionJournal = false

    private let localStore = DeviceEnrollmentLocalStore()
    private var prepared: PreparedDeviceEnrollment?
    private var signedCompletion: SignedDeviceEnrollmentCompletion?
    private var enrollmentClient: DeviceEnrollmentClient?
    private var operationTask: Task<Void, Never>?
    private var contextGeneration = UUID()
    private var autoResumedIdempotencyKeys = Set<UUID>()

    var isBusy: Bool {
        switch phase {
        case .loadingLocalState, .preparing, .signing, .completing:
            true
        default:
            false
        }
    }

    var preventsInteractiveDismissal: Bool {
        phase == .signing
    }

    var canOpenPairing: Bool {
        context != nil && phase != .loadingLocalState
    }

    var canRetrySignedCompletion: Bool {
        guard let context, let signedCompletion else { return false }
        return hasStoredCompletionJournal &&
            signedCompletion.review.observedServerID == context.serverID &&
            signedCompletion.review.audienceOrigin == context.audienceOrigin &&
            phase != .completing
    }

    var hasPreparedReview: Bool { prepared != nil && review != nil }

    var canSaveSignedCompletionJournal: Bool {
        signedCompletion != nil && !hasStoredCompletionJournal && !isBusy
    }

    func activate(_ newContext: DeviceEnrollmentContext?) async {
        guard let newContext else {
            deactivate()
            return
        }
        if context == newContext,
           ![.unavailable, .failed].contains(phase) {
            return
        }

        operationTask?.cancel()
        operationTask = nil
        prepared = nil
        signedCompletion = nil
        enrollmentClient = nil
        review = nil
        receipt = nil
        message = nil
        hasStoredCompletionJournal = false
        hasInvalidCompletionJournal = false
        context = newContext
        phase = .loadingLocalState
        contextGeneration = UUID()
        let generation = contextGeneration

        do {
            async let receiptValue = localStore.loadReceipt(serverID: newContext.serverID)
            async let journalValue = localStore.loadCompletionJournal(serverID: newContext.serverID)
            let (storedReceipt, journalData) = try await (receiptValue, journalValue)
            try Task.checkCancellation()
            guard generation == contextGeneration, context == newContext else { return }
            receipt = storedReceipt

            guard let journalData else {
                if let storedReceipt {
                    phase = storedReceipt.audienceOrigin == newContext.audienceOrigin
                        ? .paired
                        : .pairedAtAnotherOrigin
                    if storedReceipt.audienceOrigin != newContext.audienceOrigin {
                        message = "This iPhone has a paired-key receipt for \(storedReceipt.audienceOrigin), not the exact active origin. No authority is inferred here."
                    }
                } else {
                    phase = .ready
                }
                return
            }
            hasStoredCompletionJournal = true
            let signed: SignedDeviceEnrollmentCompletion
            do {
                signed = try SignedDeviceEnrollmentCompletion.resume(from: journalData)
            } catch {
                hasInvalidCompletionJournal = true
                phase = .pending
                message = "A signed completion journal exists but failed strict validation. It was preserved for explicit review or discard."
                return
            }
            guard signed.review.observedServerID == newContext.serverID else {
                hasInvalidCompletionJournal = true
                phase = .pending
                message = "The saved completion belongs to a different JoeOS installation. It was not sent or deleted."
                return
            }
            if let storedReceipt, receiptMatches(storedReceipt, signed.review) {
                receipt = storedReceipt
                do {
                    try await localStore.discardCompletionJournal(
                        serverID: newContext.serverID
                    )
                    hasStoredCompletionJournal = false
                } catch {
                    message = "The matching validated receipt is stored, but its completed retry journal could not be removed from Keychain."
                }
                phase = storedReceipt.audienceOrigin == newContext.audienceOrigin
                    ? .paired
                    : .pairedAtAnotherOrigin
                if storedReceipt.audienceOrigin != newContext.audienceOrigin {
                    message = "This iPhone has a paired-key receipt for \(storedReceipt.audienceOrigin), not the exact active origin. No authority is inferred here."
                }
                return
            }
            receipt = storedReceipt
            signedCompletion = signed
            review = signed.review
            phase = .pending
            guard signed.review.audienceOrigin == newContext.audienceOrigin else {
                message = "A signed completion is waiting for \(signed.review.audienceOrigin). Switch to that exact connection or discard it explicitly."
                return
            }
            message = "A previously signed completion was recovered from this iPhone's Keychain. Retrying the exact idempotent request does not invoke Face ID again."
            if storedReceipt != nil {
                message = "A newer signed completion is pending; an older paired receipt was preserved. Retrying uses the exact journal and does not invoke Face ID again."
            }
            if autoResumedIdempotencyKeys.insert(signed.idempotencyKey).inserted {
                startCompletion(signed, automaticallyRetryTransientFailure: false)
            }
        } catch is CancellationError {
            return
        } catch {
            guard generation == contextGeneration else { return }
            phase = .failed
            message = safeMessage(for: error, operation: .localState)
        }
    }

    func prepare(manualCode: String) {
        guard let context,
              !isBusy,
              signedCompletion == nil,
              !hasStoredCompletionJournal
        else {
            return
        }
        let candidate = manualCode.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            let parsed = try JoeOSPairingCode(candidate)
            guard parsed.audienceOrigin.value == context.audienceOrigin else {
                phase = .failed
                message = "The pairing code is for \(parsed.audienceOrigin.value), but the active JoeOS connection is exactly \(context.audienceOrigin). Nothing was sent."
                return
            }
            let client = try makeClient(serverID: context.serverID)
            enrollmentClient = client
            prepared = nil
            review = nil
            receipt = nil
            message = nil
            phase = .preparing
            let generation = contextGeneration
            operationTask?.cancel()
            operationTask = Task { [weak self] in
                guard let self else { return }
                await self.runPrepare(
                    manualCode: candidate,
                    context: context,
                    client: client,
                    generation: generation
                )
            }
        } catch {
            phase = .failed
            message = safeMessage(for: error, operation: .prepare)
        }
    }

    func confirmReviewedEnrollment() {
        guard phase == .review,
              let prepared,
              let client = enrollmentClient
        else {
            return
        }
        phase = .signing
        message = nil
        let generation = contextGeneration
        operationTask?.cancel()
        operationTask = Task { [weak self] in
            guard let self else { return }
            await self.runConfirm(
                prepared: prepared,
                client: client,
                generation: generation
            )
        }
    }

    func returnToReview() {
        guard prepared != nil, review != nil, !isBusy else { return }
        phase = .review
        message = nil
    }

    func retrySignedCompletion() {
        guard canRetrySignedCompletion,
              let signedCompletion
        else {
            return
        }
        do {
            let client = try makeClient(serverID: signedCompletion.review.observedServerID)
            enrollmentClient = client
            startCompletion(signedCompletion, automaticallyRetryTransientFailure: false)
        } catch {
            phase = .pending
            message = safeMessage(for: error, operation: .completion)
        }
    }

    func retryJournalSaveAndCompletion() {
        guard let context, let signedCompletion, !isBusy else { return }
        phase = .completing
        message = "Saving the exact signed completion before retrying the network request."
        let generation = contextGeneration
        operationTask?.cancel()
        operationTask = Task { [weak self] in
            guard let self else { return }
            do {
                let resumeData = try signedCompletion.resumeData()
                try await self.localStore.storeCompletionJournal(
                    resumeData,
                    serverID: context.serverID
                )
                try Task.checkCancellation()
                guard generation == self.contextGeneration else { return }
                self.hasStoredCompletionJournal = true
                self.hasInvalidCompletionJournal = false
                let client = try self.makeClient(serverID: context.serverID)
                self.enrollmentClient = client
                self.operationTask = nil
                self.startCompletion(
                    signedCompletion,
                    automaticallyRetryTransientFailure: false
                )
            } catch is CancellationError {
                self.phase = .pending
                self.message = "The signed completion remains in memory and was not sent."
            } catch {
                self.phase = .pending
                self.message = self.safeMessage(for: error, operation: .journal)
            }
        }
    }

    func cancelBeforeSigning() {
        guard signedCompletion == nil else { return }
        operationTask?.cancel()
        operationTask = nil
        prepared = nil
        enrollmentClient = nil
        review = nil
        phase = context == nil ? .unavailable : .cancelled
        message = "Pairing was canceled before a signed completion was saved. No authority was granted."
    }

    func resetForAnotherCode() {
        guard !isBusy, signedCompletion == nil, !hasStoredCompletionJournal else { return }
        prepared = nil
        review = nil
        message = nil
        phase = context == nil ? .unavailable : .ready
    }

    func discardPendingCompletion() async {
        guard let context, hasStoredCompletionJournal, !isBusy else { return }
        do {
            try await localStore.discardCompletionJournal(serverID: context.serverID)
            signedCompletion = nil
            hasStoredCompletionJournal = false
            hasInvalidCompletionJournal = false
            review = nil
            phase = .cancelled
            message = "The signed retry journal was explicitly discarded on this iPhone. The Halo may still contain a completed device record; inspect and revoke it from the local JoeOS console if needed."
        } catch {
            phase = .pending
            message = safeMessage(for: error, operation: .discard)
        }
    }

    func handleSheetDismissal() {
        switch phase {
        case .preparing, .review, .signing:
            cancelBeforeSigning()
        case .failed where prepared != nil:
            cancelBeforeSigning()
        default:
            break
        }
    }

    private func deactivate() {
        operationTask?.cancel()
        operationTask = nil
        contextGeneration = UUID()
        context = nil
        prepared = nil
        signedCompletion = nil
        enrollmentClient = nil
        review = nil
        receipt = nil
        message = nil
        hasStoredCompletionJournal = false
        hasInvalidCompletionJournal = false
        phase = .unavailable
    }

    private func runPrepare(
        manualCode: String,
        context: DeviceEnrollmentContext,
        client: DeviceEnrollmentClient,
        generation: UUID
    ) async {
        do {
            let clientInstanceID = try await localStore.stableClientInstanceID()
            let metadata = try makeDeviceMetadata(clientInstanceID: clientInstanceID)
            let value = try await client.prepare(
                manualCode: manualCode,
                observedServerID: context.serverID,
                device: metadata
            )
            try Task.checkCancellation()
            guard generation == contextGeneration, self.context == context else { return }
            prepared = value
            review = value.review
            phase = .review
            message = nil
        } catch is CancellationError {
            guard generation == contextGeneration else { return }
            prepared = nil
            review = nil
            phase = .cancelled
            message = "Pairing was canceled before confirmation. No signed completion was created."
        } catch {
            guard generation == contextGeneration else { return }
            prepared = nil
            review = nil
            phase = .failed
            message = safeMessage(for: error, operation: .prepare)
        }
        operationTask = nil
    }

    private func runConfirm(
        prepared: PreparedDeviceEnrollment,
        client: DeviceEnrollmentClient,
        generation: UUID
    ) async {
        do {
            let signed = try await client.confirm(prepared)
            try Task.checkCancellation()
            guard generation == contextGeneration,
                  let context,
                  signed.review.observedServerID == context.serverID,
                  signed.review.audienceOrigin == context.audienceOrigin
            else {
                throw DeviceEnrollmentLocalStoreError.invalidStoredState
            }

            signedCompletion = signed
            self.prepared = nil
            review = signed.review
            let resumeData = try signed.resumeData()
            do {
                try await localStore.storeCompletionJournal(
                    resumeData,
                    serverID: context.serverID
                )
            } catch {
                hasStoredCompletionJournal = false
                phase = .pending
                message = safeMessage(for: error, operation: .journal)
                operationTask = nil
                return
            }
            try Task.checkCancellation()
            hasStoredCompletionJournal = true
            hasInvalidCompletionJournal = false
            operationTask = nil
            startCompletion(signed, automaticallyRetryTransientFailure: true)
            return
        } catch is CancellationError {
            guard generation == contextGeneration else { return }
            phase = .cancelled
            message = "Confirmation was canceled before a signed completion was saved. Nothing was sent."
        } catch {
            guard generation == contextGeneration else { return }
            phase = .failed
            message = safeMessage(for: error, operation: .confirm)
        }
        operationTask = nil
    }

    private func startCompletion(
        _ signed: SignedDeviceEnrollmentCompletion,
        automaticallyRetryTransientFailure: Bool
    ) {
        guard let context,
              signed.review.observedServerID == context.serverID,
              signed.review.audienceOrigin == context.audienceOrigin,
              hasStoredCompletionJournal
        else {
            phase = .pending
            message = "The signed completion is preserved, but the active JoeOS origin does not match it exactly."
            return
        }
        do {
            let client = try (enrollmentClient ?? makeClient(serverID: context.serverID))
            enrollmentClient = client
            phase = .completing
            message = "The exact signed request is safely journaled in this iPhone's Keychain."
            let generation = contextGeneration
            operationTask?.cancel()
            operationTask = Task { [weak self] in
                guard let self else { return }
                await self.runCompletion(
                    signed: signed,
                    client: client,
                    generation: generation,
                    automaticallyRetryTransientFailure: automaticallyRetryTransientFailure
                )
            }
        } catch {
            phase = .pending
            message = safeMessage(for: error, operation: .completion)
        }
    }

    private func runCompletion(
        signed: SignedDeviceEnrollmentCompletion,
        client: DeviceEnrollmentClient,
        generation: UUID,
        automaticallyRetryTransientFailure: Bool
    ) async {
        do {
            let completedReceipt: DeviceEnrollmentReceipt
            do {
                completedReceipt = try await client.complete(signed)
            } catch {
                guard automaticallyRetryTransientFailure,
                      isTransientNetworkFailure(error),
                      !Task.isCancelled
                else {
                    throw error
                }
                try await Task.sleep(for: .milliseconds(350))
                completedReceipt = try await client.complete(signed)
            }
            try Task.checkCancellation()
            guard generation == contextGeneration,
                  completedReceipt.observedServerID == context?.serverID,
                  completedReceipt.audienceOrigin == context?.audienceOrigin
            else {
                throw DeviceEnrollmentLocalStoreError.invalidStoredState
            }
            let stored = try await localStore.storeReceipt(completedReceipt)
            try Task.checkCancellation()
            do {
                try await localStore.discardCompletionJournal(
                    serverID: stored.observedServerID
                )
                hasStoredCompletionJournal = false
            } catch {
                hasStoredCompletionJournal = true
                message = "The validated receipt is stored, but Keychain could not clear its completed retry journal."
            }
            signedCompletion = nil
            hasInvalidCompletionJournal = false
            receipt = stored
            review = nil
            phase = .paired
            if !hasStoredCompletionJournal {
                message = nil
            }
        } catch is CancellationError {
            guard generation == contextGeneration else { return }
            phase = .pending
            message = "The exact signed completion remains in Keychain and can be retried without Face ID."
        } catch {
            guard generation == contextGeneration else { return }
            phase = .pending
            message = safeMessage(for: error, operation: .completion)
        }
        operationTask = nil
    }

    private func makeClient(serverID: UUID) throws -> DeviceEnrollmentClient {
        #if canImport(Security)
        let prefix = "com.joeos.client.enrollment.\(serverID.uuidString.lowercased())"
        let provider = try SecureEnclaveEnrollmentKeyProvider(
            applicationTagPrefix: prefix
        )
        return DeviceEnrollmentClient(keyProvider: provider)
        #else
        throw DeviceEnrollmentError.keyGenerationFailed
        #endif
    }

    private func receiptMatches(
        _ receipt: StoredDeviceEnrollmentReceipt,
        _ review: DeviceEnrollmentReview
    ) -> Bool {
        receipt.deviceID == review.deviceID &&
            receipt.observedServerID == review.observedServerID &&
            receipt.audienceOrigin == review.audienceOrigin &&
            receipt.authenticationKeyFingerprint == review.authenticationKeyFingerprint &&
            receipt.approvalKeyFingerprint == review.approvalKeyFingerprint &&
            receipt.enrolledAt >= review.issuedAt
    }

    private func makeDeviceMetadata(clientInstanceID: UUID) throws -> EnrollmentDeviceMetadata {
        let rawName = UIDevice.current.name.trimmingCharacters(in: .whitespacesAndNewlines)
        let displayName = rawName.isEmpty || rawName.count > 80 ? "iPhone" : rawName
        let systemVersion = String(UIDevice.current.systemVersion.prefix(40))
        let rawAppVersion = Bundle.main.object(
            forInfoDictionaryKey: "CFBundleShortVersionString"
        ) as? String
        let appVersion = String((rawAppVersion ?? "1.0").prefix(40))
        do {
            return try EnrollmentDeviceMetadata(
                clientInstanceID: clientInstanceID,
                displayName: displayName,
                platform: .iOS,
                osVersion: systemVersion,
                appVersion: appVersion
            )
        } catch {
            return try EnrollmentDeviceMetadata(
                clientInstanceID: clientInstanceID,
                displayName: "iPhone",
                platform: .iOS,
                osVersion: systemVersion.isEmpty ? "iOS" : systemVersion,
                appVersion: appVersion.isEmpty ? "1.0" : appVersion
            )
        }
    }

    private enum Operation: Equatable {
        case prepare
        case confirm
        case journal
        case completion
        case localState
        case discard
    }

    private func safeMessage(for error: Error, operation: Operation) -> String {
        if let localError = error as? DeviceEnrollmentLocalStoreError {
            return localError.localizedDescription
        }
        if let enrollmentError = error as? DeviceEnrollmentError {
            if operation == .confirm {
                return "Face ID or device-key signing did not finish. Review the verified request and try again before it expires."
            }
            return enrollmentError.localizedDescription
        }
        if isTransientNetworkFailure(error) {
            if operation == .completion {
                return "JoeOS did not confirm completion. The exact signed request remains in Keychain and can be safely retried without Face ID."
            }
            return "JoeOS could not be reached. No pairing secret was saved; start or reuse a still-live local pairing window and try again."
        }
        switch operation {
        case .journal:
            "The signed completion could not be saved to this iPhone's Keychain, so it was not sent. Retry secure storage before completing."
        case .discard:
            "The signed completion remains in Keychain because it could not be deleted."
        case .localState:
            "JoeOS enrollment state could not be read safely from this iPhone's Keychain."
        case .completion:
            "JoeOS did not validate the saved completion. The exact journal remains available for review or retry."
        case .confirm:
            "Device-key confirmation did not finish. Nothing was sent."
        case .prepare:
            "The pairing request could not be verified. No signed completion was created."
        }
    }

    private func isTransientNetworkFailure(_ error: Error) -> Bool {
        let value = error as NSError
        guard value.domain == NSURLErrorDomain else { return false }
        return [
            NSURLErrorTimedOut,
            NSURLErrorNetworkConnectionLost,
            NSURLErrorNotConnectedToInternet,
            NSURLErrorCannotConnectToHost,
            NSURLErrorCannotFindHost,
            NSURLErrorDNSLookupFailed,
        ].contains(value.code)
    }
}
