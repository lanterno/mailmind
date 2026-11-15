import XCTest

/// The light's round (#44): Day, Dusk, Night and Day again, the icon the top bar's button shows in each, and the
/// `theme` preference each press keeps.
@MainActor
final class MeadowLightTests: XCTestCase {
    func testTheLightGoesRound() {
        XCTAssertEqual(MeadowLight.day.next, .dusk)
        XCTAssertEqual(MeadowLight.dusk.next, .night)
        XCTAssertEqual(MeadowLight.night.next, .day)
        XCTAssertEqual(MeadowLight.allCases.map(\.icon), [.sun, .dusk, .moon])
        XCTAssertEqual(MeadowLight.allCases.map(\.theme), [.day, .dusk, .night])
        for light in MeadowLight.allCases {
            XCTAssertEqual(MeadowLight(light.theme), light)
        }
    }

    func testTheTopBarsButtonGoesRoundTheLightsHere() {
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        XCTAssertEqual(status.light, .day, "Day until the server has said")
        status.hold(preferences: StatusFixtures.preferences())
        var seen: [MeadowLight] = []
        for _ in 0..<3 {
            status.nextLight()
            seen.append(status.light)
        }
        XCTAssertEqual(seen, [.dusk, .night, .day])
    }
}

/// The top bar's light against a real core over an empty home: each press keeps the next light as the `theme`
/// preference the page reads.
final class MeadowLightCoreTests: XCTestCase {
    /// Guards only this test's empty database.
    private static let key = "5b7d9f1a3c5e7a9b2d4f6a8c0e2b4d6f8a1c3e5b7d9f0a2c4e6b8d1f3a5c7e9b"
    private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appending(path: "mailmind-light-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
    }

    @MainActor
    func testEachPressKeepsTheNextLight() async throws {
        let standIn = folder.appending(path: "keychain.json").path(percentEncoded: false)
        let core = try await Core.open(CoreHome(folder: folder.path(percentEncoded: false),
                                                variables: ["MAILMIND_DB_KEY": Self.key, "MAILMIND_NOSYNC": "1"],
                                                standIn: standIn))
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        await status.attach(core)
        XCTAssertEqual(status.light, .day)
        for want in [MeadowLight.dusk, .night, .day] {
            await status.nextLight()?.value
            XCTAssertEqual(status.light, want)
            let kept = try await core.get(API.Get.Preferences.self).preferences.theme
            XCTAssertEqual(kept, want.theme)
        }
        status.detach()
        await core.close()
    }
}
