import Foundation
import XCTest

/// A demo's, a test's or a measure run's preferences are its own: which run keeps them apart from the person's and
/// where, and the file a run on a home of its own keeps them in, read as it starts and written as they change.
@MainActor
final class RunPreferencesTests: XCTestCase {
    private let identifier = "io.github.lanterno.mailmind"
    /// A folder standing in for the run's home, and a suite standing in for its defaults. The suite is named by a path
    /// in that folder, so it is kept there too, never among this user's preferences.
    private var home: URL!
    private var suite = ""
    /// One object, as the app's `UserDefaults.standard` is one: the file follows the changes made through it.
    private var defaults: UserDefaults!

    override func setUpWithError() throws {
        home = FileManager.default.temporaryDirectory.appending(path: "run-preferences-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        suite = home.appending(path: "defaults").path
        defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
    }

    override func tearDownWithError() throws {
        defaults.removePersistentDomain(forName: suite)
        defaults = nil
        try FileManager.default.removeItem(at: home)
    }

    // MARK: Which run keeps them apart

    /// A person's run keeps the app's own defaults, as any Mac app does: one that names no home, or an empty one.
    func testAPersonsRunKeepsTheAppsOwnDefaults() {
        XCTAssertEqual(RunPreferences.of([:], identifier: identifier), .persons)
        XCTAssertEqual(RunPreferences.of(["HOME": "/Users/ada", "PATH": "/usr/bin"], identifier: identifier), .persons)
        XCTAssertEqual(RunPreferences.of(["MAILMIND_HOME": ""], identifier: identifier), .persons)
    }

    /// A demo's or a test's run, on a temp home with the keychain's stand-in, keeps them in that home, where a Mac
    /// keeps a person's in theirs.
    func testADemoOrTestRunKeepsThemInItsHome() {
        let run = ["MAILMIND_HOME": "/private/tmp/home", "MAILMIND_TEST_KEYCHAIN": "/private/tmp/home/keychain.json",
                   "MAILMIND_DB_KEY": "00", "HOME": "/Users/ada"]
        XCTAssertEqual(RunPreferences.of(run, identifier: identifier),
                       .apart(file: URL(fileURLWithPath: "/private/tmp/home/Library/Preferences/\(identifier).plist")))
        XCTAssertEqual(RunPreferences.of(["MAILMIND_HOME": "/private/tmp/home"], identifier: identifier),
                       .apart(file: URL(fileURLWithPath: "/private/tmp/home/Library/Preferences/\(identifier).plist")),
                       "a home of its own is enough")
    }

    /// A run that isn't a person's but names no home (step 0's measure runs, a keychain's stand-in alone, a test's)
    /// keeps them apart all the same, only while it runs.
    func testARunThatNamesNoHomeKeepsThemOnlyWhileItRuns() {
        for variable in CoreHome.notAPersonsRun {
            XCTAssertEqual(RunPreferences.of([variable: "1", "HOME": "/Users/ada"], identifier: identifier),
                           .apart(file: nil), variable)
        }
        XCTAssertEqual(RunPreferences.of(["MAILMIND_MEASURE": "scroll", "MAILMIND_HOME": ""], identifier: identifier),
                       .apart(file: nil))
    }

    /// The switch that keeps them apart is on this macOS, so a run that isn't a person's opens here: where it isn't,
    /// such a run ends before it starts, saying why.
    func testThisMacOSHasTheSwitch() {
        XCTAssertNotNil(RunPreferences.volatileSwitch())
        XCTAssertTrue(RunPreferences.cannot.contains("this run opens nothing"))
    }

    /// A run kept apart restores no window a person's run left, and keeps none for the next.
    func testARunKeptApartKeepsNoWindowsState() {
        XCTAssertEqual(RunPreferences.stateless["ApplePersistenceIgnoreState"] as? Bool, true)
        XCTAssertEqual(RunPreferences.stateless["NSQuitAlwaysKeepsWindows"] as? Bool, false)
    }

    // MARK: The file a run on a home keeps them in

    private var file: URL { RunPreferences.file(home: home.path, identifier: identifier) }

    private func write(_ kept: [String: Any]) throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try PropertyListSerialization.data(fromPropertyList: kept, format: .xml, options: 0).write(to: file)
    }

    /// What a run kept is there the next time it opens on the same home, and what it sets goes back in the file whole.
    func testWhatARunKeptIsReadAgainAndWrittenBack() throws {
        try write(["meadow.rail-open": true])
        let kept = PreferencesFile(file, defaults: defaults, domain: suite)
        kept.load()
        XCTAssertTrue(defaults.bool(forKey: MeadowShell.railKey))
        defaults.set(420.0, forKey: MeadowShell.listKey)
        kept.save()
        let saved = try XCTUnwrap(PreferencesFile.read(file))
        XCTAssertEqual(saved[MeadowShell.railKey] as? Bool, true)
        XCTAssertEqual(saved[MeadowShell.listKey] as? Double, 420)
        let mode = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? Int
        XCTAssertEqual(mode, 0o600, "this user's alone, as a person's preferences are")
    }

    /// A home's first run has no file: it starts with nothing kept, and its first save makes the folders.
    func testAHomesFirstRunStartsWithNothingKept() throws {
        let kept = PreferencesFile(file, defaults: defaults, domain: suite)
        kept.load()
        XCTAssertEqual(defaults.persistentDomain(forName: suite)?.count ?? 0, 0)
        kept.save()
        XCTAssertEqual(try XCTUnwrap(PreferencesFile.read(file)).count, 0)
    }

    /// A file that isn't a property list is read as none.
    func testAFileThatDoesntReadIsNone() throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("not a property list".utf8).write(to: file)
        XCTAssertNil(PreferencesFile.read(file))
        XCTAssertNil(PreferencesFile.read(home.appending(path: "none.plist")))
    }

    /// The shell's rail and the list's width, set as a person sets them, reach the run's file a moment later, with no
    /// save asked for: whoever changes the defaults, the file follows.
    func testWhatTheShellSetsReachesTheFileOnItsOwn() async throws {
        let kept = PreferencesFile(file, defaults: defaults, domain: suite)
        kept.watch()
        let shell = MeadowShell(defaults: defaults)
        shell.toggleRail()
        shell.setListWidth(ShellLayout.listStart + 40)
        var saved: [String: Any]?
        for _ in 0..<200 {
            saved = PreferencesFile.read(file)
            if saved?[MeadowShell.listKey] != nil { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        XCTAssertEqual(saved?[MeadowShell.railKey] as? Bool, shell.railOpen)
        XCTAssertEqual(saved?[MeadowShell.listKey] as? Double, Double(shell.listWidth))
    }
}
