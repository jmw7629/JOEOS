import JoeOSCore
import SwiftUI

struct CommandCenterView: View {
    @AppStorage(ConnectionProfileStorage.profilesKey)
    private var encodedProfiles = ConnectionProfileStorage.defaultPayload

    @AppStorage(ConnectionProfileStorage.activeProfileKey)
    private var activeProfileID = ConnectionProfile.defaultVPS.id.uuidString

    @StateObject private var session = BrowserSession()
    @StateObject private var enrollment = DeviceEnrollmentCoordinator()
    @State private var reloadToken = UUID()
    @State private var isShowingSettings = false
    @State private var isShowingPairing = false

    var body: some View {
        ZStack {
            Color.joeOSCanvas.ignoresSafeArea()
            VStack(spacing: 0) {
                commandChrome
                loadingBar
                content
            }
        }
        .sheet(isPresented: $isShowingSettings) {
            ConnectionSettingsView(
                encodedProfiles: $encodedProfiles,
                activeProfileID: $activeProfileID
            )
        }
        .sheet(isPresented: $isShowingPairing) {
            DevicePairingSheet(enrollment: enrollment)
        }
        .onAppear(perform: repairActiveProfileIfNeeded)
        .onChange(of: encodedProfiles) { _, _ in
            isShowingPairing = false
            repairActiveProfileIfNeeded()
            reloadToken = UUID()
        }
        .onChange(of: activeProfileID) { _, _ in
            isShowingPairing = false
            reloadToken = UUID()
        }
        .task(id: discoveryTaskID) {
            await session.discoverBootstrap(from: validatedEndpoint)
        }
        .task(id: enrollmentTaskID) {
            await enrollment.activate(enrollmentContext)
        }
    }

    private var commandChrome: some View {
        VStack(spacing: 10) {
            HStack(spacing: 12) {
                Image("JoeOSMark")
                    .resizable()
                    .scaledToFit()
                    .frame(width: 38, height: 38)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .accessibilityHidden(true)

                VStack(alignment: .leading, spacing: 2) {
                    Text("JOEOS")
                        .font(.system(size: 16, weight: .heavy, design: .rounded))
                        .tracking(1.2)
                    Text(activeProfile.displayName)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                Spacer(minLength: 8)

                statusPill

                Button(action: refresh) {
                    Image(systemName: "arrow.clockwise")
                        .frame(width: 32, height: 32)
                }
                .buttonStyle(CommandIconButtonStyle())
                .disabled(session.isLoading || validatedEndpoint == nil)
                .accessibilityLabel("Refresh JoeOS")

                Button {
                    isShowingSettings = true
                } label: {
                    Image(systemName: "gearshape.fill")
                        .frame(width: 32, height: 32)
                }
                .buttonStyle(CommandIconButtonStyle())
                .accessibilityLabel("Open connection settings")
            }

            if let host = validatedEndpoint?.origin.host {
                HStack(spacing: 7) {
                    Image(systemName: validatedEndpoint?.origin.scheme == "https" ? "lock.fill" : "network")
                    Text(host)
                        .lineLimit(1)
                    Spacer()
                    Text(validatedEndpoint?.origin.scheme == "https" ? "Encrypted transport" : "Private-network HTTP")
                        .foregroundStyle(validatedEndpoint?.origin.scheme == "https" ? Color.joeOSGreen : Color.joeOSWarning)
                }
                .font(.caption2.monospaced())
                .foregroundStyle(.secondary)
                .accessibilityElement(children: .combine)
            }

            BootstrapPostureRow(state: session.bootstrapState)

            DeviceEnrollmentStatusRow(enrollment: enrollment) {
                isShowingPairing = true
            }
        }
        .padding(.horizontal, 14)
        .padding(.top, 9)
        .padding(.bottom, 10)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(Color.white.opacity(0.08))
                .frame(height: 1)
        }
    }

    @ViewBuilder
    private var loadingBar: some View {
        if session.isLoading {
            ProgressView(value: max(0.05, session.progress))
                .progressViewStyle(.linear)
                .tint(Color.joeOSCyan)
                .accessibilityLabel("Loading JoeOS")
                .accessibilityValue("\(Int(session.progress * 100)) percent")
        }
    }

    @ViewBuilder
    private var content: some View {
        if let endpoint = validatedEndpoint {
            ZStack(alignment: .top) {
                JoeOSWebView(
                    endpoint: endpoint.url,
                    reloadToken: reloadToken,
                    session: session
                )
                .id(endpoint.url.absoluteString)

                VStack(spacing: 10) {
                    if let notice = session.policyNotice {
                        PolicyNoticeBanner(message: notice) {
                            session.clearPolicyNotice()
                        }
                    }

                    if session.phase == .offline || session.phase == .error {
                        ConnectionErrorCard(
                            title: session.statusTitle,
                            message: session.statusDetail,
                            retry: refresh,
                            settings: { isShowingSettings = true }
                        )
                    }
                }
                .padding(14)
            }
        } else {
            InvalidConnectionView(
                message: endpointValidationMessage,
                openSettings: { isShowingSettings = true }
            )
        }
    }

    private var statusPill: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(statusColor)
                .frame(width: 7, height: 7)
                .shadow(color: statusColor.opacity(0.75), radius: 4)
            Text(session.statusTitle.uppercased())
                .font(.system(size: 10, weight: .bold, design: .rounded))
                .tracking(0.7)
        }
        .foregroundStyle(statusColor)
        .padding(.horizontal, 9)
        .frame(height: 32)
        .background(statusColor.opacity(0.09), in: Capsule())
        .overlay(Capsule().stroke(statusColor.opacity(0.22), lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Connection status: \(session.statusTitle)")
    }

    private var statusColor: Color {
        switch session.phase {
        case .idle: .secondary
        case .loading: .joeOSCyan
        case .online: .joeOSGreen
        case .offline: .joeOSWarning
        case .error: .joeOSError
        }
    }

    private var profiles: [ConnectionProfile] {
        ConnectionProfileStorage.decode(encodedProfiles)
    }

    private var activeProfile: ConnectionProfile {
        guard let id = UUID(uuidString: activeProfileID) else { return profiles[0] }
        return profiles.first(where: { $0.id == id }) ?? profiles[0]
    }

    private var endpointResult: Result<ValidatedEndpoint, EndpointValidationError> {
        EndpointPolicy.validate(activeProfile.endpoint)
    }

    private var validatedEndpoint: ValidatedEndpoint? {
        try? endpointResult.get()
    }

    private var endpointValidationMessage: String {
        guard case .failure(let error) = endpointResult else {
            return "The selected connection is unavailable."
        }
        return error.localizedDescription
    }

    private var discoveryTaskID: String {
        (validatedEndpoint?.url.absoluteString ?? "invalid") + "#" + reloadToken.uuidString
    }

    private var enrollmentContext: DeviceEnrollmentContext? {
        guard case .validated(let contract) = session.bootstrapState,
              let endpoint = validatedEndpoint
        else {
            return nil
        }
        return DeviceEnrollmentContext(
            contract: contract,
            endpoint: endpoint,
            profileName: activeProfile.name
        )
    }

    private var enrollmentTaskID: String {
        enrollmentContext?.identity ?? "pairing-unavailable#\(discoveryTaskID)"
    }

    private func repairActiveProfileIfNeeded() {
        if !profiles.contains(where: { $0.id.uuidString == activeProfileID }) {
            activeProfileID = profiles[0].id.uuidString
        }
    }

    private func refresh() {
        reloadToken = UUID()
    }
}

private struct BootstrapPostureRow: View {
    let state: BootstrapDiscoveryState

    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            postureIcon
                .frame(width: 16, height: 16)
                .padding(.top, 1)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                Text(detail)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 6)

            if let badge {
                Text(badge)
                    .font(.system(size: 8, weight: .bold, design: .rounded))
                    .tracking(0.5)
                    .foregroundStyle(tint)
                    .padding(.horizontal, 6)
                    .frame(height: 20)
                    .background(tint.opacity(0.08), in: Capsule())
                    .overlay(Capsule().stroke(tint.opacity(0.2), lineWidth: 1))
            }
        }
        .padding(9)
        .background(Color.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 11, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 11, style: .continuous)
                .stroke(tint.opacity(0.14), lineWidth: 1)
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel("JoeOS discovery status: \(title). \(detail)")
    }

    @ViewBuilder
    private var postureIcon: some View {
        switch state {
        case .checking:
            ProgressView()
                .controlSize(.mini)
                .tint(Color.joeOSCyan)
        default:
            Image(systemName: iconName)
                .font(.caption.weight(.semibold))
                .foregroundStyle(tint)
        }
    }

    private var title: String {
        switch state {
        case .idle:
            "Native discovery ready"
        case .checking:
            "Checking JoeOS contract"
        case .validated(let contract):
            "\(contract.displayName) · v\(contract.serverVersion)"
        case .legacyServer:
            "Native discovery unavailable"
        case .unavailable:
            "Discovery temporarily unreachable"
        case .rejected:
            "Bootstrap contract not validated"
        }
    }

    private var detail: String {
        switch state {
        case .idle:
            "The web command center can load before discovery completes."
        case .checking:
            "The web command center is loading independently; no trust is granted while this check runs."
        case .validated(let contract):
            if !contract.supportsLocalConsolePairing || contract.hasApplicationAuthentication || contract.hasRoleBasedAccess || contract.hasPrivilegedActions {
                return "Unexpected privileged posture reported; native controls remain disabled."
            }
            return "Local-console pairing is supported, but this iPhone is not enrolled. App authentication, roles, and privileged approvals are unavailable; the server UUID is informational only."
        case .legacyServer:
            "This may be an older JoeOS server. The web command center remains available, but native identity and security posture are unverified."
        case .unavailable:
            "The web command center remains available. No enrollment, authentication, or authorization is inferred."
        case .rejected:
            "The response did not match the strict same-origin JoeOS contract. Web access is unaffected; native trust and privileged controls remain disabled."
        }
    }

    private var badge: String? {
        switch state {
        case .validated:
            "CONTRACT VALIDATED"
        case .legacyServer:
            "WEB ONLY"
        case .rejected:
            "NOT VALIDATED"
        default:
            nil
        }
    }

    private var iconName: String {
        switch state {
        case .idle:
            "shield"
        case .checking:
            "hourglass"
        case .validated:
            "checkmark.shield.fill"
        case .legacyServer:
            "clock.badge.questionmark"
        case .unavailable:
            "wifi.exclamationmark"
        case .rejected:
            "xmark.shield.fill"
        }
    }

    private var tint: Color {
        switch state {
        case .validated:
            .joeOSGreen
        case .rejected:
            .joeOSError
        case .legacyServer, .unavailable:
            .joeOSWarning
        default:
            .joeOSCyan
        }
    }
}

private struct CommandIconButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(configuration.isPressed ? Color.white : Color.joeOSCyan)
            .background(
                Color.white.opacity(configuration.isPressed ? 0.13 : 0.055),
                in: RoundedRectangle(cornerRadius: 10, style: .continuous)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(Color.white.opacity(0.09), lineWidth: 1)
            )
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

private struct ConnectionErrorCard: View {
    let title: String
    let message: String
    let retry: () -> Void
    let settings: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: "wifi.exclamationmark")
                    .foregroundStyle(Color.joeOSWarning)
                Text(title)
                    .font(.headline)
                Spacer()
            }
            Text(message)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Retry", action: retry)
                    .buttonStyle(.borderedProminent)
                    .tint(Color.joeOSBlue)
                Button("Connections", action: settings)
                    .buttonStyle(.bordered)
            }
            Text("Retrying does not execute commands or change the Halo.")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .padding(16)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(Color.joeOSWarning.opacity(0.26), lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(0.26), radius: 18, y: 8)
        .accessibilityElement(children: .contain)
    }
}

private struct PolicyNoticeBanner: View {
    let message: String
    let dismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "shield.lefthalf.filled")
                .foregroundStyle(Color.joeOSWarning)
            Text(message)
                .font(.footnote)
                .foregroundStyle(.secondary)
            Spacer(minLength: 6)
            Button(action: dismiss) {
                Image(systemName: "xmark")
                    .font(.caption.bold())
            }
            .accessibilityLabel("Dismiss navigation notice")
        }
        .padding(12)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.joeOSWarning.opacity(0.22), lineWidth: 1)
        )
    }
}

private struct InvalidConnectionView: View {
    let message: String
    let openSettings: () -> Void

    var body: some View {
        ContentUnavailableView {
            Label("Connection needs attention", systemImage: "network.badge.shield.half.filled")
        } description: {
            Text(message)
        } actions: {
            Button("Open Connections", action: openSettings)
                .buttonStyle(.borderedProminent)
                .tint(Color.joeOSBlue)
        }
        .background(Color.joeOSCanvas)
    }
}
