#if os(macOS)
import AppKit
import SwiftUI

/// The first run: no account yet. A welcome that leads to adding one (the page's `firstrun`), and what AI needs: a
/// Claude API key, or nothing, and AI stays off.
struct Welcome: View {
    let status: AppStatus
    @State private var adding = false
    @State private var keySet: Bool?

    init(status: AppStatus, keySet: Bool? = nil) {
        self.status = status
        _keySet = State(initialValue: keySet)
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 22) {
                Image(nsImage: NSApplication.shared.applicationIconImage)
                    .resizable()
                    .frame(width: 96, height: 96)
                    .accessibilityHidden(true)
                VStack(spacing: 6) {
                    Text("Welcome to MailMind")
                        .font(.largeTitle.weight(.semibold))
                    Text("No email accounts connected yet.")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }
                Button {
                    adding = true
                } label: {
                    Label("Connect Your First Account…", systemImage: "plus")
                        .padding(.horizontal, 6)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)
                SettingHelp(String(localized: "Gmail, Proton through Proton Bridge, or any IMAP server. Its password is kept in your login Keychain, never in a file."))
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 420)

                GroupBox {
                    VStack(alignment: .leading, spacing: 10) {
                        Label("AI, if you want it", systemImage: "sparkles")
                            .font(.headline)
                        SettingHelp(String(localized: """
                            Summaries, the morning brief, drafted replies and asking your mail are written by Claude, \
                            through the Claude API, with an API key of yours. Without one, AI is off and nothing is sent: \
                            everything else works as it does with it.
                            """))
                        ClaudeKeyView(status: status, keySet: $keySet)
                        SettingHelp(StatusWords.aiPrivacy)
                    }
                    .padding(6)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxWidth: 520)
            }
            .padding(40)
            .frame(maxWidth: .infinity)
        }
        .background(.background)
        .sheet(isPresented: $adding) {
            AccountForm(status: status, draft: AccountDraft()) { _ in adding = false }
        }
    }
}
#endif
