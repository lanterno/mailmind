import Foundation
import ServiceManagement
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
}
