import Foundation
import XCTest

/// The core hears of a wake and of a network that really changed, once for a burst, and never of a path that is how
/// things already stood, that reaches nowhere, or that is the same as before.
@MainActor
final class WakingTests: XCTestCase {
    private let wifi = Waking.Shape(reaches: true, interfaces: ["wifi en0"], gateways: ["192.0.2.1:0"])
    private let otherWifi = Waking.Shape(reaches: true, interfaces: ["wifi en0"], gateways: ["198.51.100.1:0"])
    private let wired = Waking.Shape(reaches: true, interfaces: ["wiredEthernet en5", "wifi en0"],
                                     gateways: ["192.0.2.1:0"])
    private let nowhere = Waking.Shape(reaches: false)

    func testThePathTheCoreOpenedOnIsntAChange() {
        var paths = Waking.Paths()
        XCTAssertFalse(paths.heard(wifi), "the first path is how things already stood")
        XCTAssertFalse(paths.heard(wifi), "the same path again changes nothing")
    }

    func testAnotherNetworkOrAnotherInterfaceIsAChange() {
        var paths = Waking.Paths()
        _ = paths.heard(wifi)
        XCTAssertTrue(paths.heard(otherWifi), "another Wi-Fi, through another gateway")
        XCTAssertTrue(paths.heard(wired), "a cable plugged in")
        XCTAssertFalse(paths.heard(wired))
    }

    func testANetworkLostIsToldWhenOneComesBackEvenTheSame() {
        var paths = Waking.Paths()
        _ = paths.heard(wifi)
        XCTAssertFalse(paths.heard(nowhere), "nothing to reach: nothing to do until a path comes back")
        XCTAssertTrue(paths.heard(wifi), "back on the same Wi-Fi, IDLE's connections are dead all the same")
    }

    func testTheOrderOfInterfacesAndGatewaysIsNoChange() {
        var paths = Waking.Paths()
        _ = paths.heard(Waking.Shape(reaches: true, interfaces: ["wifi en0", "other utun3"], gateways: ["a", "b"]))
        XCTAssertFalse(paths.heard(Waking.Shape(reaches: true, interfaces: ["other utun3", "wifi en0"],
                                                gateways: ["b", "a"])))
    }

    func testABurstIsToldOnceAfterItsFirst() async throws {
        var told = 0
        let waking = Waking(delay: .milliseconds(60)) { told += 1 }
        waking.heard(wifi)
        waking.tell()
        waking.heard(otherWifi)
        waking.tell()
        XCTAssertEqual(told, 0, "the core hears it once the burst is over, not at its first")
        try await Task.sleep(for: .milliseconds(300))
        XCTAssertEqual(told, 1, "a wake and a network that changed with it are told once")
        waking.heard(wired)
        try await Task.sleep(for: .milliseconds(300))
        XCTAssertEqual(told, 2, "a change after the burst is told again")
        waking.heard(wired)
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(told, 2, "the same path again is not")
    }
}
