import XCTest

/// The light's round (#44): Automatic, Day, Dusk, Night and Automatic again, the icon the top bar's button shows in
/// each light, the `theme` preference each press keeps, and the light Automatic paints in.
@MainActor
final class MeadowLightTests: XCTestCase {
    func testTheThemesGoRound() {
        XCTAssertEqual(API.Theme.auto.next, .day)
        XCTAssertEqual(API.Theme.day.next, .dusk)
        XCTAssertEqual(API.Theme.dusk.next, .night)
        XCTAssertEqual(API.Theme.night.next, .auto)
        XCTAssertEqual(API.Theme.round.map(\.name), ["Automatic", "Day", "Dusk", "Night"])
        XCTAssertEqual(MeadowLight.allCases.map(\.icon), [.sun, .dusk, .moon])
        XCTAssertEqual(MeadowLight.allCases.map(\.theme), [.day, .dusk, .night])
        for light in MeadowLight.allCases {
            XCTAssertEqual(MeadowLight(light.theme), light)
            XCTAssertEqual(light.theme.light(systemDark: true, hour: 12), light, "a light chosen stays in Dark Mode")
        }
    }

    /// Night while the system is dark; else Day from 6, Dusk from 17 and Night from 21, as the page's lib/light.ts.
    func testAutomaticIsNightInDarkModeAndElseTheHours() {
        let hours = (0..<24).map { MeadowLight.automatic(systemDark: false, hour: $0) }
        XCTAssertEqual(hours, Array(repeating: .night, count: 6) + Array(repeating: .day, count: 11)
            + Array(repeating: .dusk, count: 4) + Array(repeating: .night, count: 3))
        for hour in 0..<24 {
            XCTAssertEqual(API.Theme.auto.light(systemDark: true, hour: hour), .night)
        }
    }

    func testTheTopBarsButtonGoesRoundTheThemesHere() {
        let status = Self.status()
        status.systemDark = true
        XCTAssertEqual(status.theme, .auto, "Automatic until the server has said")
        XCTAssertEqual(status.light, .night, "Automatic is Night in Dark Mode")
        status.hold(preferences: StatusFixtures.preferences())
        var seen: [API.Theme] = []
        var lit: [MeadowLight] = []
        for _ in 0..<4 {
            status.nextLight()
            seen.append(status.theme)
            lit.append(status.light)
        }
        XCTAssertEqual(seen, [.dusk, .night, .auto, .day])
        XCTAssertEqual(lit, [.dusk, .night, .night, .day])
    }

    /// The window follows the minute: Automatic at noon is Day, and Dusk once the status's clock says 18:00.
    func testAutomaticFollowsTheStatusesClock() throws {
        let status = Self.status()
        var preferences = StatusFixtures.preferences()
        preferences.theme = .auto
        let noon = try XCTUnwrap(Calendar.current.date(bySettingHour: 12, minute: 0, second: 0, of: Date()))
        status.hold(preferences: preferences, now: noon)
        XCTAssertEqual(status.light, .day)
        status.hold(preferences: preferences, now: noon.addingTimeInterval(6 * 3600))
        XCTAssertEqual(status.light, .dusk)
        status.systemDark = true
        XCTAssertEqual(status.light, .night)
    }

    private static func status() -> AppStatus {
        AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true }, badge: { _ in },
                                         announce: { _ in }))
    }
}

/// The top bar's light against a real core over an empty home: a fresh install is Automatic, and each press keeps
/// the next choice as the `theme` preference the page reads.
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
    func testAFreshInstallIsAutomaticAndEachPressKeepsTheNextChoice() async throws {
        let standIn = folder.appending(path: "keychain.json").path(percentEncoded: false)
        let core = try await Core.open(CoreHome(folder: folder.path(percentEncoded: false),
                                                variables: ["MAILMIND_DB_KEY": Self.key, "MAILMIND_NOSYNC": "1"],
                                                standIn: standIn))
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        await status.attach(core)
        XCTAssertEqual(status.theme, .auto)
        for want in [API.Theme.day, .dusk, .night, .auto] {
            await status.nextLight()?.value
            XCTAssertEqual(status.theme, want)
            let kept = try await core.get(API.Get.Preferences.self).preferences.theme
            XCTAssertEqual(kept, want)
        }
        status.detach()
        await core.close()
    }
}
