import Foundation

/// Remote-session policy for the JoeOS Mobile Companion.
///
/// The iOS client is a client of authoritative JoeOS services. This module
/// validates the local policy boundary: session shape, refresh-token
/// requirements, offline-action safety, and deep-link allowlists. It never
/// makes network calls and never stores credentials; it exists so the client
/// can enforce the same boundaries the server enforces.
public enum MobileCompanionPolicy {

    /// Commands the mobile client may request remotely. Anything outside this
    /// allowlist is rejected locally before it reaches the network.
    public static let allowedRemoteCommands: Set<String> = [
        "view_system_status",
        "view_projects",
        "view_missions",
        "view_tasks",
        "view_agents",
        "view_notifications",
        "view_communications",
        "view_approvals",
        "view_workflows",
        "view_runtime_health",
        "view_models",
        "acknowledge_notification",
        "respond_internal",
        "create_note",
        "create_task_proposal",
        "pause_task",
        "pause_mission",
        "trigger_workflow",
        "select_model",
        "request_test",
        "request_build",
        "request_desktop_handoff",
        "approve_low_risk",
        "deny_action",
    ]

    /// Commands that must never be submitted from a mobile client.
    public static let prohibitedRemoteCommands: Set<String> = [
        "arbitrary_command",
        "shell_execute",
        "spawn_process",
        "git_push",
        "deployment",
        "file_deletion",
        "service_restart",
        "secret_access",
        "modify_trust",
        "grant_permission",
    ]

    /// Actions that may be queued offline safely. High-risk operations are
    /// never queued; they must be revalidated and confirmed online.
    public static let offlineSafeActions: Set<String> = [
        "mark_notification_read",
        "acknowledge_notification",
        "archive_routine_item",
        "draft_internal_reply",
        "create_note",
        "create_task_proposal",
        "update_checklist",
        "request_handoff",
    ]

    /// Actions that must never be queued offline.
    public static let offlineProhibitedActions: Set<String> = [
        "destructive_approval",
        "external_send_approval",
        "git_push",
        "deployment",
        "file_deletion",
        "service_restart",
        "secret_access",
        "arbitrary_command",
        "high_risk_task_cancellation",
    ]

    /// Deep-link target types that are allowed to open a review screen.
    /// A deep link may open a review surface; it never executes an action.
    public static let allowedDeepLinkTargets: Set<String> = [
        "notification",
        "approval",
        "mission",
        "task",
        "agent",
        "workflow_run",
        "project",
        "patch",
        "build",
        "test",
        "device",
        "handoff",
    ]

    /// Whether a mobile client may request a command remotely.
    public static func allowsRemoteCommand(_ command: String) -> Bool {
        allowedRemoteCommands.contains(command)
    }

    /// Whether a command is prohibited outright for mobile clients.
    public static func prohibitsRemoteCommand(_ command: String) -> Bool {
        prohibitedRemoteCommands.contains(command)
    }

    /// Whether an action may be queued for offline replay.
    public static func allowsOffline(_ action: String) -> Bool {
        offlineSafeActions.contains(action)
    }

    /// Whether an action is explicitly prohibited from offline queueing.
    public static func prohibitsOffline(_ action: String) -> Bool {
        offlineProhibitedActions.contains(action)
    }

    /// Whether a deep-link target may be opened (as a review screen only).
    public static func allowsDeepLink(target: String) -> Bool {
        allowedDeepLinkTargets.contains(target)
    }
}

/// App-lock policy for the JoeOS mobile client.
///
/// A local unlock (biometric or device passcode) protects the locally stored
/// credential. It never independently renews or authorizes a remote host
/// session; host authentication remains required.
public enum MobileAppLockPolicy {
    /// Views that require a recent local unlock before they are shown.
    public static let sensitiveViews: Set<String> = [
        "approval",
        "private_communication",
        "repository_content",
        "secrets_adjacent",
    ]

    public static func requiresRecentUnlock(for view: String) -> Bool {
        sensitiveViews.contains(view)
    }
}

/// Version negotiation between the mobile client and the JoeOS host.
public struct MobileAPIVersion: Equatable, Sendable {
    public let major: Int
    public let minor: Int
    public let patch: Int

    public init(major: Int, minor: Int, patch: Int) {
        self.major = major
        self.minor = minor
        self.patch = patch
    }
}
