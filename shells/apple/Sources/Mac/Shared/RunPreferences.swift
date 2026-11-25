#if os(macOS)
import Foundation

/// Where this run keeps its preferences: what a person sets (read only, the rail, the list's width, a Tidy run
/// dismissed), the frames AppKit saves for the windows, and whatever else the app or the system's frameworks put in
/// the app's defaults.
///
/// Every Release build of MailMind is `io.github.lanterno.mailmind`, so macOS hands a demo, a measure run or a test's
/// run the defaults of the person's own MailMind: such a run would read their window frames, and could turn their
/// read-only switch on. (The Debug build, MailMind Dev, is `io.github.lanterno.mailmind.dev`, a domain of its own, and
/// is kept apart all the same.) So a run that isn't a person's (`CoreHome.isAPersonsRun`) makes this process's user defaults volatile as
/// it starts, before anything reads one: nothing of the person's is read or written. It carries in the Mac's own
/// settings (Dark Mode, language and region), and keeps its own in its home, where a Mac keeps a person's in theirs
/// (`<MAILMIND_HOME>/Library/Preferences/<identifier>.plist`), read as it starts and written as they change. A run that
/// names no home keeps them only while it runs.
///
/// `CFFIXED_USER_HOME` doesn't do this: on macOS 26 it moves `NSHomeDirectory()`, but the defaults stay where they
/// were.
enum RunPreferences: Equatable {
    /// A person's run: the app's own defaults, as any Mac app has.
    case persons
    /// Kept apart from the person's, in memory, and in `file` when the run names a home.
    case apart(file: URL?)

    /// Where the run `environment` describes keeps its preferences, for the app `identifier`.
    static func of(_ environment: [String: String], identifier: String) -> RunPreferences {
        if CoreHome.isAPersonsRun(environment) { return .persons }
        guard let home = environment["MAILMIND_HOME"], !home.isEmpty else { return .apart(file: nil) }
        return .apart(file: file(home: home, identifier: identifier))
    }

    /// The file a run on `home` keeps its preferences in.
    static func file(home: String, identifier: String) -> URL {
        URL(fileURLWithPath: home, isDirectory: true).appending(path: "Library/Preferences/\(identifier).plist")
    }

    /// What a run kept apart puts in its defaults before AppKit reads them: no window's state is restored from a
    /// person's run or kept for the next one (AppKit writes what it would keep into this user's temporary folder).
    static var stateless: [String: Any] { ["ApplePersistenceIgnoreState": true, "NSQuitAlwaysKeepsWindows": false] }

    /// Why a run that isn't a person's doesn't open, on a macOS that can't keep its preferences apart.
    static let cannot = """
        MailMind keeps a demo's, a test's or a measure run's preferences apart from yours, and this macOS has no way \
        left to do that, so this run opens nothing. Open MailMind yourself to use it.
        """

    /// The kept file, saved once more as the process ends.
    nonisolated(unsafe) private static var kept: PreferencesFile?

    /// Keeps this run's preferences apart from a person's, when it isn't one: called first thing, before the app or
    /// any framework reads a preference. Where this macOS can't, the run ends here, saying why.
    static func keepApart(_ environment: [String: String] = ProcessInfo.processInfo.environment,
                          identifier: String = Bundle.main.bundleIdentifier ?? CoreHome.folderName)
    {
        guard case .apart(let file) = of(environment, identifier: identifier) else { return }
        guard let volatile = volatileSwitch() else {
            FileHandle.standardError.write(Data((cannot + "\n").utf8))
            exit(78)
        }
        volatile()
        carryTheMacsSettings()
        UserDefaults.standard.register(defaults: stateless)
        guard let file else { return }
        let kept = PreferencesFile(file, defaults: .standard, domain: identifier)
        kept.load()
        kept.save()
        kept.watch()
        Self.kept = kept
        atexit { RunPreferences.kept?.save() }
    }

    typealias Switch = @convention(c) () -> Void

    /// Core Foundation's own switch for this, `_CFPreferencesAlwaysUseVolatileUserDomains`: once thrown, every user
    /// domain of this process is held in memory, never read from disk or written to it. It is looked up rather than
    /// linked, so a macOS without it is known: nil.
    static func volatileSwitch() -> Switch? {
        let symbol = dlsym(dlopen(nil, RTLD_NOW), "_CFPreferencesAlwaysUseVolatileUserDomains")
        return symbol.map { unsafeBitCast($0, to: Switch.self) }
    }

    /// The Mac's own settings, the person's global domain (Dark Mode, language and region, a title bar's
    /// double-click), carried in from its file now that the domain is volatile. Never written back; a change made in
    /// System Settings while this runs isn't seen.
    private static func carryTheMacsSettings() {
        guard let entry = getpwuid(getuid()), let home = entry.pointee.pw_dir else { return }
        let file = URL(fileURLWithPath: String(cString: home), isDirectory: true)
            .appending(path: "Library/Preferences/.GlobalPreferences.plist")
        guard let global = PreferencesFile.read(file) else { return }
        CFPreferencesSetMultiple(global as CFDictionary, nil, kCFPreferencesAnyApplication, kCFPreferencesCurrentUser,
                                 kCFPreferencesAnyHost)
    }
}

/// A run's preferences in a file of its own: the domain `domain` of `defaults` read from it as the run starts, and
/// written back to it whole as they change (a moment after a change, once for a burst such as a window dragged) and as
/// the run ends.
final class PreferencesFile: @unchecked Sendable {
    let file: URL
    private let defaults: UserDefaults
    private let domain: String
    private let queue = DispatchQueue(label: "io.github.lanterno.mailmind.run-preferences")
    /// A save is on its way (read and written on `queue`).
    private var due = false
    private var watching: NSObjectProtocol?

    /// How long after a change the file is written.
    static let settle = DispatchTimeInterval.milliseconds(300)

    init(_ file: URL, defaults: UserDefaults, domain: String) {
        self.file = file
        self.defaults = defaults
        self.domain = domain
    }

    deinit {
        if let watching { NotificationCenter.default.removeObserver(watching) }
    }

    /// A property list file's dictionary; nil when there is none or it doesn't read as one.
    static func read(_ file: URL) -> [String: Any]? {
        guard let data = try? Data(contentsOf: file) else { return nil }
        return (try? PropertyListSerialization.propertyList(from: data, format: nil)) as? [String: Any]
    }

    /// What the file holds, into the domain. Nothing when it isn't there yet.
    func load() {
        if let kept = Self.read(file) { defaults.setPersistentDomain(kept, forName: domain) }
    }

    /// The domain, into the file, whole, readable by this user alone as a person's preferences are.
    func save() {
        let kept = defaults.persistentDomain(forName: domain) ?? [:]
        guard let data = try? PropertyListSerialization.data(fromPropertyList: kept, format: .xml, options: 0) else {
            return
        }
        let files = FileManager.default
        try? files.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true,
                                   attributes: [.posixPermissions: 0o700])
        if (try? data.write(to: file, options: .atomic)) != nil {
            try? files.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        }
    }

    /// Saves a moment after each change to `defaults`, whoever made it.
    func watch() {
        watching = NotificationCenter.default.addObserver(forName: UserDefaults.didChangeNotification, object: defaults,
                                                          queue: nil) { [weak self] _ in self?.soon() }
    }

    private func soon() {
        queue.async { [self] in
            guard !due else { return }
            due = true
            queue.asyncAfter(deadline: .now() + Self.settle) { [self] in
                due = false
                save()
            }
        }
    }
}
#endif
