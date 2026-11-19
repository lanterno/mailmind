// Where MailMind starts. A demo's, a test's or a measure run's preferences are made its own first
// (Mac/Shared/RunPreferences.swift), before the app or any of the system's frameworks reads one.
#if os(macOS)
RunPreferences.keepApart()
#endif
MainActor.assumeIsolated { MailMindApp.main() }
