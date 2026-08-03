import Foundation
import XCTest
@testable import JoeOSCore

final class MobileCompanionPolicyTests: XCTestCase {
    func testAllowlistedRemoteCommand() {
        XCTAssertTrue(MobileCompanionPolicy.allowsRemoteCommand("view_system_status"))
        XCTAssertTrue(MobileCompanionPolicy.allowsRemoteCommand("acknowledge_notification"))
    }

    func testUnknownRemoteCommandRejected() {
        XCTAssertFalse(MobileCompanionPolicy.allowsRemoteCommand("sudo_rm"))
    }

    func testProhibitedRemoteCommandRejected() {
        XCTAssertTrue(MobileCompanionPolicy.prohibitsRemoteCommand("shell_execute"))
        XCTAssertTrue(MobileCompanionPolicy.prohibitsRemoteCommand("git_push"))
        XCTAssertTrue(MobileCompanionPolicy.prohibitsRemoteCommand("grant_permission"))
        XCTAssertFalse(MobileCompanionPolicy.allowsRemoteCommand("shell_execute"))
    }

    func testOfflineSafeActions() {
        XCTAssertTrue(MobileCompanionPolicy.allowsOffline("create_note"))
        XCTAssertTrue(MobileCompanionPolicy.allowsOffline("mark_notification_read"))
    }

    func testOfflineProhibitedActions() {
        XCTAssertTrue(MobileCompanionPolicy.prohibitsOffline("destructive_approval"))
        XCTAssertTrue(MobileCompanionPolicy.prohibitsOffline("external_send_approval"))
        XCTAssertTrue(MobileCompanionPolicy.prohibitsOffline("git_push"))
        XCTAssertFalse(MobileCompanionPolicy.allowsOffline("destructive_approval"))
    }

    func testDeepLinkAllowlist() {
        XCTAssertTrue(MobileCompanionPolicy.allowsDeepLink(target: "approval"))
        XCTAssertTrue(MobileCompanionPolicy.allowsDeepLink(target: "mission"))
        XCTAssertFalse(MobileCompanionPolicy.allowsDeepLink(target: "shell"))
    }

    func testAppLockSensitiveViews() {
        XCTAssertTrue(MobileAppLockPolicy.requiresRecentUnlock(for: "approval"))
        XCTAssertTrue(MobileAppLockPolicy.requiresRecentUnlock(for: "private_communication"))
        XCTAssertFalse(MobileAppLockPolicy.requiresRecentUnlock(for: "home"))
    }
}
