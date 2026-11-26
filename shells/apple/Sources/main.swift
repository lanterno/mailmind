// Where MailMind starts. A demo's, a test's or a measure run's preferences are made its own first
// (Mac/Shared/RunPreferences.swift), before the app or any of the system's frameworks reads one.
#if os(macOS)
RunPreferences.keepApart()
#if DEBUG
// MailMind Dev under the end-to-end framework's driver (Sources/Dev), when MAILMIND_TEST_DRIVER names its runner.
DevDriver.startIfAsked()
#endif
#endif
MainActor.assumeIsolated { MailMindApp.main() }
