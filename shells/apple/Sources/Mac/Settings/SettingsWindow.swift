#if os(macOS)
import SwiftUI

/// MailMind's Settings window, the page's Settings as a Mac's: the accounts, then what the page calls Preferences,
/// split as a Mac splits them. Everything is the server's: each control saves on its own, as the page's does.
struct SettingsWindow: View {
    @Bindable var status: AppStatus

    var body: some View {
        Group {
            if status.core == nil {
                ProgressView("Opening MailMind")
                    .frame(width: 480, height: 240)
            } else {
                TabView(selection: $status.settingsTab) {
                    Tab("Accounts", systemImage: "at", value: SettingsTab.accounts) {
                        AccountsPane(status: status)
                    }
                    Tab("General", systemImage: "gearshape", value: SettingsTab.general) {
                        GeneralPane(status: status)
                    }
                    Tab("Notifications", systemImage: "bell.badge", value: SettingsTab.notifications) {
                        NotificationsPane(status: status)
                    }
                    Tab("AI", systemImage: "sparkles", value: SettingsTab.ai) {
                        AIPane(status: status)
                    }
                }
                .scenePadding()
            }
        }
        .overlay(alignment: .bottom) { NoticeBanner(status: status) }
    }
}

/// The help under a setting, as the page's `set-help` lines.
struct SettingHelp: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.callout)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// A secret kept in the login Keychain (the Claude API key, the Proton Bridge's password): whether one is set and where
/// the one in use comes from, never what it is; a field to keep a new one, which goes straight to its route and is
/// emptied once sent; and a way to take the kept one out.
struct KeptSecretView: View {
    let label: String
    let prompt: String
    let standing: SecretStanding?
    /// Keep what was typed; the words a refusal gave, or nil.
    let keep: (String) async -> String?
    let remove: () async -> String?
    let removeWarning: String

    @State private var typed = ""
    @State private var working = false
    @State private var problem: String?
    @State private var asking = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let standing {
                Label(standing.summary, systemImage: standing.kept ? "lock.fill" : "lock.open")
                    .foregroundStyle(standing.kept ? .primary : .secondary)
                if let note = standing.note { SettingHelp(note) }
            } else {
                ProgressView().controlSize(.small)
            }
            HStack {
                SecureField(label, text: $typed, prompt: Text(prompt))
                    .labelsHidden()
                    .onSubmit { send() }
                    .accessibilityLabel(label)
                Button(standing?.kept == true ? "Replace" : "Keep") { send() }
                    .disabled(typed.isEmpty || working)
                if standing?.kept == true {
                    Button("Remove…", role: .destructive) { asking = true }
                        .disabled(working)
                }
            }
            if let problem {
                Text(problem).font(.callout).foregroundStyle(.red)
            }
        }
        .confirmationDialog("Remove the \(label.lowercased()) from the Keychain?", isPresented: $asking) {
            Button("Remove", role: .destructive) {
                Task {
                    working = true
                    problem = await remove()
                    working = false
                }
            }
        } message: {
            Text(removeWarning)
        }
    }

    /// The typed secret goes to its route at once, and the field lets go of it whatever the answer.
    private func send() {
        guard !typed.isEmpty, !working else { return }
        let secret = typed
        typed = ""
        working = true
        Task {
            problem = await keep(secret)
            working = false
        }
    }
}

/// The few words on what just happened, for a moment, at the foot of the window.
struct NoticeBanner: View {
    let status: AppStatus

    var body: some View {
        if let notice = status.notice {
            Text(notice.text)
                .font(.callout)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(.regularMaterial, in: Capsule())
                .overlay(Capsule().strokeBorder(.separator))
                .padding(.bottom, 14)
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .id(notice.id)
                .accessibilityAddTraits(.updatesFrequently)
        }
    }
}
#endif
