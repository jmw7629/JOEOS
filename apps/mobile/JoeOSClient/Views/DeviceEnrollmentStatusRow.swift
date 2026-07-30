import SwiftUI

struct DeviceEnrollmentStatusRow: View {
    @ObservedObject var enrollment: DeviceEnrollmentCoordinator
    let open: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            statusIcon
                .frame(width: 18, height: 18)
                .padding(.top, 1)

            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.primary)
                Text(detail)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 8)

            if enrollment.canOpenPairing {
                Button(action: open) {
                    Text(actionTitle)
                        .font(.system(size: 9, weight: .bold, design: .rounded))
                        .tracking(0.45)
                        .foregroundStyle(tint)
                        .padding(.horizontal, 8)
                        .frame(minHeight: 24)
                        .background(tint.opacity(0.09), in: Capsule())
                        .overlay(Capsule().stroke(tint.opacity(0.25), lineWidth: 1))
                }
                .buttonStyle(.plain)
                .disabled(enrollment.phase == .signing)
                .accessibilityLabel(actionAccessibilityLabel)
            }
        }
        .padding(10)
        .background(Color.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 11, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 11, style: .continuous)
                .stroke(tint.opacity(0.16), lineWidth: 1)
        )
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder
    private var statusIcon: some View {
        if enrollment.isBusy {
            ProgressView()
                .controlSize(.mini)
                .tint(tint)
        } else {
            Image(systemName: iconName)
                .font(.caption.weight(.semibold))
                .foregroundStyle(tint)
        }
    }

    private var title: String {
        switch enrollment.phase {
        case .unavailable:
            "Native pairing unavailable"
        case .loadingLocalState:
            "Checking this iPhone"
        case .ready:
            "This iPhone is not paired"
        case .preparing:
            "Verifying local pairing window"
        case .review:
            "Pairing review required"
        case .signing:
            "Confirming device keys"
        case .completing:
            "Completing key pairing"
        case .pending:
            "Signed completion safely pending"
        case .paired:
            "Paired keys · no authority"
        case .pairedAtAnotherOrigin:
            "Paired at another exact origin"
        case .cancelled:
            "Pairing canceled"
        case .failed:
            "Pairing needs attention"
        }
    }

    private var detail: String {
        switch enrollment.phase {
        case .unavailable:
            "Validate a schema-v2 JoeOS connection before native enrollment can begin."
        case .loadingLocalState:
            "Reading only this device's non-synchronizing Keychain state."
        case .ready:
            "Create a five-minute code on the Halo, then review before Face ID signs anything."
        case .preparing:
            "Checking the exact origin, server proof, transcript, and both public keys. No signing yet."
        case .review:
            "Review the bound server, origin, keys, and expiry. Face ID runs only after confirmation."
        case .signing:
            "Face ID protects the approval-key signature. No command or privileged action is being approved."
        case .completing:
            "The exact signed request is journaled for an idempotent retry."
        case .pending:
            enrollment.message ?? "Retry the exact signed request without another Face ID prompt."
        case .paired:
            "Locally stored state is active_unassigned: no session, role, approval, or execution permission."
        case .pairedAtAnotherOrigin:
            enrollment.message ?? "The stored receipt does not match this active origin exactly."
        case .cancelled, .failed:
            enrollment.message ?? "Open pairing details to continue safely."
        }
    }

    private var actionTitle: String {
        switch enrollment.phase {
        case .ready, .cancelled:
            "PAIR"
        case .review:
            "REVIEW"
        case .pending:
            "RESUME"
        case .paired, .pairedAtAnotherOrigin:
            "DETAILS"
        case .failed:
            "REVIEW"
        default:
            "OPEN"
        }
    }

    private var actionAccessibilityLabel: String {
        switch enrollment.phase {
        case .paired, .pairedAtAnotherOrigin:
            "Show paired device details"
        case .pending:
            "Review pending signed enrollment completion"
        default:
            "Open native device pairing"
        }
    }

    private var iconName: String {
        switch enrollment.phase {
        case .paired:
            "checkmark.shield.fill"
        case .pairedAtAnotherOrigin:
            "shield.lefthalf.filled.badge.checkmark"
        case .pending, .review:
            "person.badge.key.fill"
        case .failed:
            "exclamationmark.shield.fill"
        case .cancelled:
            "xmark.shield"
        case .unavailable:
            "lock.slash"
        default:
            "iphone.gen3.badge.play"
        }
    }

    private var tint: Color {
        switch enrollment.phase {
        case .paired:
            .joeOSGreen
        case .failed:
            .joeOSError
        case .pending, .pairedAtAnotherOrigin, .cancelled:
            .joeOSWarning
        case .unavailable:
            .secondary
        default:
            .joeOSCyan
        }
    }
}
