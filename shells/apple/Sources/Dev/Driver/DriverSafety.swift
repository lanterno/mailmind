#if DEBUG && os(macOS)
import Foundation

/// When MailMind Dev's driver may start, safe by construction (docs/test-architecture.md section 6): only in MailMind
/// Dev, on a home in the system's temp folder, with the Keychain's stand-in, the core's clock paused and a database key
/// of the run's own. Anything else is a run that could reach a person's mail, their Keychain or their time, so the
/// driver refuses it and the app ends there.
enum DriverSafety {
    /// MailMind Dev's identity: the Debug build's, never the one a person installs.
    static let identity = "io.github.lanterno.mailmind.dev"

    /// Why the driver won't start in this run, or nil when it may. `temporary` is the system's temp folder.
    static func refusal(_ environment: [String: String], identifier: String?, temporary: String) -> String? {
        guard identifier == identity else {
            return "the driver runs only in MailMind Dev (\(identity)), not \(identifier ?? "an app with no identifier")"
        }
        guard let home = environment["MAILMIND_HOME"], !home.isEmpty else {
            return "the driver needs MAILMIND_HOME: a home of the run's own in the system's temp folder"
        }
        guard within(home, temporary) else {
            return "the driver runs only on a home in the system's temp folder (\(temporary)), not \(home)"
        }
        guard !(environment["MAILMIND_TEST_KEYCHAIN"] ?? "").isEmpty else {
            return "the driver needs MAILMIND_TEST_KEYCHAIN: the Keychain's stand-in, never the login Keychain"
        }
        guard environment["MAILMIND_TEST_CLOCK"] == "paused" else {
            return "the driver needs MAILMIND_TEST_CLOCK=paused: the core's test clock, its jobs paused"
        }
        guard !(environment["MAILMIND_DB_KEY"] ?? "").isEmpty else {
            return "the driver needs MAILMIND_DB_KEY: a database key of the run's own"
        }
        return nil
    }

    /// Whether `path` is inside `folder` once both are resolved (`/var` is `/private/var` on a Mac), never the folder
    /// itself.
    static func within(_ path: String, _ folder: String) -> Bool {
        let inner = resolved(path), outer = resolved(folder)
        guard !outer.isEmpty, outer != "/" else { return false }
        let prefix = outer.hasSuffix("/") ? outer : outer + "/"
        return inner.hasPrefix(prefix) && !inner.split(separator: "/").contains("..")
    }

    private static func resolved(_ path: String) -> String {
        let standard = URL(fileURLWithPath: path).standardizedFileURL.resolvingSymlinksInPath().path
        // Foundation gives `/private/var` and `/private/tmp` back as `/var` and `/tmp`: one spelling of each.
        for short in ["/var", "/tmp"] where standard == short || standard.hasPrefix(short + "/") {
            return "/private" + standard
        }
        return standard
    }
}
#endif
