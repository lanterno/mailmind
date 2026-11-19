import AppKit
import Foundation
import ServiceManagement
import SwiftUI
import XCTest

/// Settings › General's Open at Login, over a stand-in for macOS's login items: a test never makes itself one.
@MainActor
final class OpenAtLoginTests: XCTestCase {
    /// What macOS would hold, and what it was asked.
    private final class Held: LoginItem {
        var status: SMAppService.Status = .notRegistered
        var asks: [Bool] = []
        /// What macOS answers when asked to register: enabled, waiting for approval, or a refusal.
        var registers: SMAppService.Status? = .enabled

        func register() throws {
            asks.append(true)
            guard let registers else { throw CocoaError(.featureUnsupported) }
            status = registers
        }

        func unregister() throws {
            asks.append(false)
            status = .notRegistered
        }
    }

    func testItStartsAsMacOSHoldsIt() {
        let held = Held()
        XCTAssertFalse(OpenAtLogin(item: held).isOn)
        held.status = .enabled
        XCTAssertTrue(OpenAtLogin(item: held).isOn)
    }

    func testTurningItOnAndOffAsksMacOS() {
        let held = Held()
        let model = OpenAtLogin(item: held)
        model.set(true)
        XCTAssertTrue(model.isOn)
        XCTAssertFalse(model.needsApproval)
        model.set(false)
        XCTAssertFalse(model.isOn)
        XCTAssertEqual(held.asks, [true, false])
        XCTAssertNil(model.problem)
    }

    func testOneWaitingToBeAllowedIsOnAndSaysWhere() {
        let held = Held()
        held.registers = .requiresApproval
        let model = OpenAtLogin(item: held)
        model.set(true)
        XCTAssertTrue(model.isOn, "asked for, while macOS waits for it to be allowed")
        XCTAssertTrue(model.needsApproval)
    }

    func testARefusalLeavesItOffAndSaysWhatToDo() {
        let held = Held()
        held.registers = nil
        let model = OpenAtLogin(item: held)
        model.set(true)
        XCTAssertFalse(model.isOn)
        XCTAssertEqual(model.problem?.contains("System Settings › General › Login Items"), true)
    }

    func testAChangeMadeInSystemSettingsIsReadAgain() {
        let held = Held()
        let model = OpenAtLogin(item: held)
        held.status = .enabled
        model.refresh()
        XCTAssertTrue(model.isOn)
    }

    func testItsHelpSaysWhyItMatters() {
        XCTAssertTrue(OpenAtLogin.help.contains("mail you scheduled goes at its time"))
    }

    /// A run that isn't a person's (a demo's, a test's, a measure run) has no login item: it would make its own build
    /// one of the person's. Its switch stays off whatever is asked, and macOS is never asked.
    func testARunThatIsntAPersonsHasNoSwitch() throws {
        let home = FileManager.default.temporaryDirectory.appending(path: "mm-demo-\(UUID().uuidString)").path
        for run in [["MAILMIND_HOME": home, "MAILMIND_TEST_KEYCHAIN": home + "/keychain.json"],
                    ["MAILMIND_MEASURE": "home"], ["XCTestConfigurationFilePath": "x"]] {
            let model = OpenAtLogin(run)
            XCTAssertFalse(model.available, "\(run.keys.sorted())")
            model.set(true)
            model.refresh()
            XCTAssertFalse(model.isOn)
            XCTAssertFalse(model.needsApproval)
            XCTAssertNil(model.problem)
        }
        XCTAssertFalse(OpenAtLogin().available, "this test's own run")
        XCTAssertTrue(OpenAtLogin(item: Held()).available)
    }

    /// Settings › General shows the switch off and turned off, and says why under it.
    func testItsCardSaysWhyInARunThatIsntAPersons() async throws {
        _ = NSApplication.shared
        MeadowVoiceOverTests.assistive(true)
        defer { MeadowVoiceOverTests.assistive(false) }
        let card = OpenAtLoginCard(model: OpenAtLogin(["MAILMIND_HOME": "/nowhere"]))
        let host = NSHostingView(rootView: card.frame(width: 520, height: 300))
        host.frame = CGRect(x: 0, y: 0, width: 520, height: 300)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        window.layoutIfNeeded()
        try await Task.sleep(for: .milliseconds(300))

        let elements = Self.walk(host)
        let said = elements.map { "\($0.role) \"\($0.label)\" \($0.enabled ? "" : "(off)")" }.joined(separator: "\n")
        XCTAssertTrue(elements.contains { $0.label.contains("This demo or test run never opens at login") }, said)
        let toggle = try XCTUnwrap(elements.first { [NSAccessibility.Role.checkBox.rawValue, "AXSwitch"]
                .contains($0.role) }, said)
        XCTAssertFalse(toggle.enabled, "the switch can't be turned on:\n\(said)")
        XCTAssertEqual(OpenAtLogin.notAvailable, """
            This demo or test run never opens at login. Open at login is a switch here when you open MailMind \
            yourself.
            """)
    }

    /// Each element under `element`: its role, its words and whether it can be used.
    private static func walk(_ element: Any) -> [(role: String, label: String, enabled: Bool)] {
        let object = element as AnyObject
        let role = (object.accessibilityRole?() ?? nil)?.rawValue ?? ""
        let label = [object.accessibilityLabel?() ?? nil, object.accessibilityTitle?() ?? nil, words(of: object)]
            .compactMap { $0 }.joined(separator: " ")
        let enabled = object.isAccessibilityEnabled?() ?? true
        let kids = (object.accessibilityChildren?() ?? nil) ?? []
        return [(role, label, enabled)] + kids.flatMap { walk($0) }
    }

    /// A text's words, which SwiftUI gives as its value, read through the getter.
    private static func words(of object: AnyObject) -> String? {
        guard let object = object as? NSObject, object.responds(to: NSSelectorFromString("accessibilityValue")) else {
            return nil
        }
        let value = object.value(forKey: "accessibilityValue")
        return (value as? String) ?? (value as? NSAttributedString)?.string
    }
}
