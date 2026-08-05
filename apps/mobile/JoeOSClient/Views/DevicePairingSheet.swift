import SwiftUI

struct DevicePairingSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var enrollment: DeviceEnrollmentCoordinator

    @State private var manualCode = ""
    @State private var confirmsDiscard = false
    @FocusState private var codeFieldFocused: Bool

    var body: some View {
        NavigationStack {
            ZStack {
                LinearGradient(
                    colors: [Color.joeOSCanvas, Color.joeOSPanel.opacity(0.96)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                .ignoresSafeArea()

                ScrollView {
                    VStack(spacing: 16) {
                        identityHeader
                        phaseContent
                        authorityBoundary
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 18)
                }
                .scrollDismissesKeyboard(.interactively)
            }
            .navigationTitle("Pair This iPhone")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Color.joeOSPanel, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(toolbarTitle, action: close)
                        .disabled(enrollment.phase == .signing)
                }
            }
        }
        .preferredColorScheme(.dark)
        .interactiveDismissDisabled(enrollment.preventsInteractiveDismissal)
        .confirmationDialog(
            "Discard the signed completion?",
            isPresented: $confirmsDiscard,
            titleVisibility: .visible
        ) {
            Button("Discard Keychain Journal", role: .destructive) {
                Task { await enrollment.discardPendingCompletion() }
            }
            Button("Keep for Safe Retry", role: .cancel) {}
        } message: {
            Text("The VPS may already have completed this device record. Discarding only removes this iPhone's retry journal; inspect or revoke the device from the local JoeOS console if needed.")
        }
        .onDisappear {
            clearManualCode()
            enrollment.handleSheetDismissal()
        }
    }

    private var identityHeader: some View {
        VStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(Color.joeOSCyan.opacity(0.11))
                    .frame(width: 66, height: 66)
                    .overlay(Circle().stroke(Color.joeOSCyan.opacity(0.26), lineWidth: 1))
                Image(systemName: "iphone.gen3.radiowaves.left.and.right")
                    .font(.system(size: 27, weight: .semibold))
                    .foregroundStyle(Color.joeOSCyan)
                    .shadow(color: Color.joeOSCyan.opacity(0.45), radius: 10)
            }

            VStack(spacing: 4) {
                Text("LOCAL DEVICE ENROLLMENT")
                    .font(.system(size: 11, weight: .heavy, design: .rounded))
                    .tracking(1.2)
                    .foregroundStyle(Color.joeOSCyan)
                Text(enrollment.context?.serverName ?? "JoeOS Command Center")
                    .font(.title3.weight(.semibold))
                Text(enrollment.context?.audienceOrigin ?? "Waiting for a validated server")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var phaseContent: some View {
        switch enrollment.phase {
        case .unavailable:
            statePanel(
                icon: "lock.slash",
                title: "Pairing is not available",
                detail: "Return to the command center and validate a JoeOS schema-v2 private connection first.",
                tint: .joeOSWarning
            )
        case .loadingLocalState:
            progressPanel(
                title: "Checking this iPhone",
                detail: "Reading server-scoped receipt and retry state from the non-synchronizing Keychain."
            )
        case .ready:
            codeEntry
        case .preparing:
            progressPanel(
                title: "Verifying before any signature",
                detail: "JoeOS is checking the exact origin, local secret proof, bounded transcript, expiry, and two public keys. Face ID has not been invoked."
            )
        case .review:
            reviewPanel
        case .signing:
            progressPanel(
                title: "Confirm with Face ID",
                detail: "The approval key is signing only this verified enrollment transcript. This is not approval for a command or privileged action."
            )
        case .completing:
            progressPanel(
                title: "Completing idempotently",
                detail: enrollment.message ?? "The exact signed completion was saved to this iPhone's Keychain before the network request."
            )
        case .pending:
            pendingPanel
        case .paired, .pairedAtAnotherOrigin:
            receiptPanel
        case .cancelled:
            messagePanel(tint: .joeOSWarning)
            codeEntry
        case .failed:
            messagePanel(tint: .joeOSError)
            if enrollment.hasPreparedReview {
                Button("Return to Verified Review") {
                    enrollment.returnToReview()
                }
                .buttonStyle(PairingPrimaryButtonStyle(tint: .joeOSCyan))
            } else if enrollment.hasStoredCompletionJournal {
                pendingActions
            } else {
                codeEntry
            }
        }
    }

    private var codeEntry: some View {
        PairingGlassPanel {
            VStack(alignment: .leading, spacing: 14) {
                Label("Paste the one-use VPS code", systemImage: "key.viewfinder")
                    .font(.headline)

                VStack(alignment: .leading, spacing: 7) {
                    instruction(number: "1", text: "On the VPS, run the local JoeOS iPhone pairing tool.")
                    instruction(number: "2", text: "Paste the full JOEOS1 code here while its five-minute window is open.")
                }

                SecureField("JOEOS1|https://…", text: $manualCode)
                    .focused($codeFieldFocused)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textContentType(.oneTimeCode)
                    .font(.system(.body, design: .monospaced))
                    .padding(13)
                    .background(Color.black.opacity(0.26), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .stroke(Color.joeOSCyan.opacity(codeFieldFocused ? 0.5 : 0.18), lineWidth: 1)
                    )
                    .privacySensitive()
                    .onSubmit(submitCode)
                    .accessibilityLabel("One-use JoeOS pairing code")

                Button(action: submitCode) {
                    Label("Verify Pairing Window", systemImage: "checkmark.shield.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(PairingPrimaryButtonStyle(tint: .joeOSBlue))
                .disabled(!isCodeLengthPlausible || enrollment.isBusy)

                Label(
                    "The code is cleared from this form as soon as verification starts. JoeOS Client never writes the manual code, pairing secret, or derived key to storage.",
                    systemImage: "lock.doc.fill"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder
    private var reviewPanel: some View {
        if let review = enrollment.review {
            PairingGlassPanel {
                VStack(alignment: .leading, spacing: 15) {
                    HStack {
                        Label("Verified request", systemImage: "checkmark.seal.fill")
                            .font(.headline)
                            .foregroundStyle(Color.joeOSGreen)
                        Spacer()
                        TimelineView(.periodic(from: .now, by: 1)) { context in
                            Text(expiryText(review.expiresAt, now: context.date))
                                .font(.caption.monospacedDigit().weight(.semibold))
                                .foregroundStyle(review.expiresAt > context.date ? Color.joeOSWarning : Color.joeOSError)
                        }
                    }

                    PairingDetailRow(label: "Exact origin", value: review.audienceOrigin)
                    PairingDetailRow(
                        label: "Server ID",
                        value: review.observedServerID.uuidString.lowercased()
                    )
                    PairingDetailRow(
                        label: "Authentication key",
                        value: abbreviated(review.authenticationKeyFingerprint)
                    )
                    PairingDetailRow(
                        label: "Approval key",
                        value: abbreviated(review.approvalKeyFingerprint)
                    )

                    Text("Two distinct non-exportable P-256 keys are bound to this installation. Face ID is requested only when you press the confirmation button below.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)

                    Button {
                        enrollment.confirmReviewedEnrollment()
                    } label: {
                        Label("Confirm Keys with Face ID", systemImage: "faceid")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(PairingPrimaryButtonStyle(tint: .joeOSCyan))
                    .disabled(review.expiresAt <= Date())

                    Button("Cancel Before Signing", role: .cancel) {
                        enrollment.cancelBeforeSigning()
                    }
                    .frame(maxWidth: .infinity)
                    .buttonStyle(.bordered)
                }
            }
        }
    }

    private var pendingPanel: some View {
        VStack(spacing: 12) {
            statePanel(
                icon: enrollment.hasInvalidCompletionJournal ? "exclamationmark.shield.fill" : "arrow.triangle.2.circlepath.circle.fill",
                title: enrollment.hasInvalidCompletionJournal ? "Journal requires review" : "Exact completion saved",
                detail: enrollment.message ?? "The signed request is preserved in the ThisDeviceOnly Keychain and can be retried without signing again.",
                tint: enrollment.hasInvalidCompletionJournal ? .joeOSError : .joeOSWarning
            )
            pendingActions
        }
    }

    private var pendingActions: some View {
        PairingGlassPanel {
            VStack(spacing: 11) {
                if enrollment.canRetrySignedCompletion {
                    Button {
                        enrollment.retrySignedCompletion()
                    } label: {
                        Label("Retry Exact Completion", systemImage: "arrow.clockwise.shield")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(PairingPrimaryButtonStyle(tint: .joeOSBlue))

                    Text("This reuses the same signed body and idempotency key. Face ID is not invoked again.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else if enrollment.canSaveSignedCompletionJournal {
                    Button {
                        enrollment.retryJournalSaveAndCompletion()
                    } label: {
                        Label("Save Journal, Then Complete", systemImage: "lock.badge.checkmark")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(PairingPrimaryButtonStyle(tint: .joeOSBlue))
                }

                if enrollment.hasStoredCompletionJournal {
                    Button("Discard Pending Journal", role: .destructive) {
                        confirmsDiscard = true
                    }
                    .buttonStyle(.bordered)
                }
            }
        }
    }

    @ViewBuilder
    private var receiptPanel: some View {
        if let receipt = enrollment.receipt {
            PairingGlassPanel {
                VStack(alignment: .leading, spacing: 14) {
                    Label(
                        enrollment.phase == .paired ? "Device keys paired" : "Receipt belongs to another origin",
                        systemImage: enrollment.phase == .paired ? "checkmark.shield.fill" : "arrow.left.arrow.right.circle.fill"
                    )
                    .font(.headline)
                    .foregroundStyle(enrollment.phase == .paired ? Color.joeOSGreen : Color.joeOSWarning)

                    Text("ACTIVE_UNASSIGNED")
                        .font(.system(size: 11, weight: .heavy, design: .rounded))
                        .tracking(0.9)
                        .foregroundStyle(Color.joeOSCyan)
                        .padding(.horizontal, 9)
                        .padding(.vertical, 5)
                        .background(Color.joeOSCyan.opacity(0.09), in: Capsule())
                        .overlay(Capsule().stroke(Color.joeOSCyan.opacity(0.23), lineWidth: 1))

                    PairingDetailRow(label: "Paired origin", value: receipt.audienceOrigin)
                    PairingDetailRow(label: "Device ID", value: receipt.deviceID.uuidString.lowercased())
                    PairingDetailRow(
                        label: "Enrolled",
                        value: receipt.enrolledAt.formatted(date: .abbreviated, time: .shortened)
                    )

                    if let message = enrollment.message {
                        Text(message)
                            .font(.footnote)
                            .foregroundStyle(Color.joeOSWarning)
                    }

                    Text("This is a locally stored, server-validated key receipt. JoeOS does not yet provide an authenticated session or a live revocation check in the app. Manage revocation from the VPS's local console.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var authorityBoundary: some View {
        PairingGlassPanel {
            VStack(alignment: .leading, spacing: 10) {
                Label("PAIRING IS NOT AUTHORIZATION", systemImage: "hand.raised.fill")
                    .font(.system(size: 11, weight: .heavy, design: .rounded))
                    .tracking(0.65)
                    .foregroundStyle(Color.joeOSWarning)

                HStack(spacing: 8) {
                    boundaryBadge("NO SESSION")
                    boundaryBadge("NO ROLE")
                }
                HStack(spacing: 8) {
                    boundaryBadge("NO APPROVAL")
                    boundaryBadge("NO EXECUTION")
                }

                Text("Pairing only records device public keys as active_unassigned. It cannot run an agent, shell, download, Git action, deployment, payment, message, or remote command.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func statePanel(icon: String, title: String, detail: String, tint: Color) -> some View {
        PairingGlassPanel {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: icon)
                    .font(.title3)
                    .foregroundStyle(tint)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 5) {
                    Text(title).font(.headline)
                    Text(detail)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
        }
    }

    private func progressPanel(title: String, detail: String) -> some View {
        PairingGlassPanel {
            HStack(alignment: .top, spacing: 13) {
                ProgressView()
                    .tint(Color.joeOSCyan)
                    .controlSize(.regular)
                    .padding(.top, 2)
                VStack(alignment: .leading, spacing: 5) {
                    Text(title).font(.headline)
                    Text(detail)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
        }
    }

    private func messagePanel(tint: Color) -> some View {
        statePanel(
            icon: enrollment.phase == .failed ? "exclamationmark.triangle.fill" : "info.circle.fill",
            title: enrollment.phase == .failed ? "Pairing did not finish" : "Pairing canceled safely",
            detail: enrollment.message ?? "No authority was granted.",
            tint: tint
        )
    }

    private func instruction(number: String, text: String) -> some View {
        HStack(alignment: .top, spacing: 9) {
            Text(number)
                .font(.caption2.bold())
                .foregroundStyle(Color.joeOSCanvas)
                .frame(width: 20, height: 20)
                .background(Color.joeOSCyan, in: Circle())
            Text(text)
                .font(.footnote)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func boundaryBadge(_ value: String) -> some View {
        Text(value)
            .font(.system(size: 9, weight: .bold, design: .rounded))
            .tracking(0.45)
            .foregroundStyle(Color.joeOSWarning)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 7)
            .background(Color.joeOSWarning.opacity(0.07), in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.joeOSWarning.opacity(0.17), lineWidth: 1))
    }

    private var toolbarTitle: String {
        switch enrollment.phase {
        case .preparing, .review:
            "Cancel"
        default:
            "Close"
        }
    }

    private var isCodeLengthPlausible: Bool {
        (80...400).contains(manualCode.trimmingCharacters(in: .whitespacesAndNewlines).count)
    }

    private func submitCode() {
        guard isCodeLengthPlausible else { return }
        let value = manualCode
        clearManualCode()
        codeFieldFocused = false
        enrollment.prepare(manualCode: value)
    }

    private func close() {
        clearManualCode()
        switch enrollment.phase {
        case .preparing, .review:
            enrollment.cancelBeforeSigning()
        case .failed where enrollment.hasPreparedReview:
            enrollment.cancelBeforeSigning()
        default:
            break
        }
        dismiss()
    }

    private func clearManualCode() {
        manualCode.removeAll(keepingCapacity: false)
    }

    private func abbreviated(_ value: String) -> String {
        guard value.count > 18 else { return value }
        return "\(value.prefix(10))…\(value.suffix(8))"
    }

    private func expiryText(_ expiry: Date, now: Date) -> String {
        let seconds = max(0, Int(expiry.timeIntervalSince(now)))
        return seconds > 0 ? "\(seconds)s left" : "expired"
    }
}

private struct PairingGlassPanel<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .stroke(Color.white.opacity(0.09), lineWidth: 1)
            )
            .shadow(color: Color.black.opacity(0.22), radius: 16, y: 8)
    }
}

private struct PairingDetailRow: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label.uppercased())
                .font(.system(size: 9, weight: .bold, design: .rounded))
                .tracking(0.5)
                .foregroundStyle(.tertiary)
            Text(value)
                .font(.caption.monospaced())
                .foregroundStyle(.primary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct PairingPrimaryButtonStyle: ButtonStyle {
    let tint: Color

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(Color.white)
            .padding(.horizontal, 16)
            .frame(minHeight: 48)
            .background(tint.opacity(configuration.isPressed ? 0.68 : 0.9), in: RoundedRectangle(cornerRadius: 13, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 13, style: .continuous)
                    .stroke(Color.white.opacity(0.14), lineWidth: 1)
            )
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}
