#if os(macOS)
import SwiftUI

/// MailMind Help, from the Help menu (the page's `Help`): where the keys are, the search operators, how MailMind uses
/// AI, the one setting WCAG 2.1.4 asks for (single keys off), and the guide again.
struct HelpWindow: View {
    static let id = "mailmind-help"
    let status: AppStatus

    /// The page's operators, as its legend lists them.
    static let operators = [
        "from:", "to:", "cc:", "subject:", "body:", "is:unread", "is:flagged", "category:finance", "is:newsletter",
        "is:snoozed", "bucket:receipt", "has:attachment", "in:Sent", "account:", "before:", "after:",
        "\"quoted phrase\"", "-exclude",
    ]

    var body: some View {
        Form {
            Section("Keyboard") {
                Text("Every command is in the menu bar, with its keys beside it.")
                Toggle("Disable single-key shortcuts (use ⌘ chords only)", isOn: Binding(
                    get: { !(status.preferences?.singleKeys ?? true) },
                    set: { status.setPreferences(.init(singleKeys: !$0)) }))
                    .disabled(status.preferences == nil)
            }
            Section("Search operators") {
                Text(Self.operators.map { "`\($0)`" }.joined(separator: "  ").markdown)
                    .lineSpacing(4)
                    .textSelection(.enabled)
            }
            Section {
                Text(Self.howAI)
                    .fixedSize(horizontal: false, vertical: true)
            } header: {
                Label("How MailMind uses AI", systemImage: "wind")
            }
            Section {
                Button("Show Me Around") { status.guiding = true }
            }
        }
        .formStyle(.grouped)
        .frame(width: 520, height: 560)
    }

    static let howAI = String(localized: """
        Summaries, importance scores, the daily briefing, drafts, and ask-your-inbox are written by Claude, through \
        the Claude API, when an API key is set; without one, AI is off and nothing is sent. Only the text of the mail \
        being summarized, drafted or asked about is sent to Anthropic; your mailbox itself stays in a database on this \
        Mac. Email bodies show inside a locked sandbox that can't run code or phone home.
        """)
}

private extension String {
    var markdown: AttributedString { (try? AttributedString(markdown: self)) ?? AttributedString(self) }
}

/// The first-run guide's steps and when it is offered (status/guideSteps.ts). The Mac has no coach-marks to place:
/// each step is a page of a sheet, with the symbol of what it is about.
enum Guide {
    struct Step: Equatable, Identifiable, Sendable {
        let id: String
        let symbol: String
        let title: String
        let text: String
    }

    /// Held to what is true on this install: a key that's switched off isn't taught, nor what needs AI that isn't on.
    static func steps(ai: Bool, singleKeys: Bool) -> [Step] {
        var steps = [
            Step(id: "brief", symbol: "sun.max", title: String(localized: "Today’s brief"),
                 text: String(localized: "The brief says what needs you, what’s safe to file, and what’s coming up.")),
            Step(id: "rail", symbol: "sidebar.left", title: String(localized: "Folders, and your accounts"),
                 text: singleKeys
                    ? String(localized: "Your accounts and folders are in the sidebar. Open a message and read it beside the list; j and k move through the list.")
                    : String(localized: "Your accounts and folders are in the sidebar. Open a message and read it beside the list.")),
            Step(id: "palette", symbol: "command", title: String(localized: "⌘K opens every command"),
                 text: String(localized: "Folders, filing, settings and more, by name. Press ⌘K any time, and start typing.")),
        ]
        if ai {
            steps.append(Step(id: "ask", symbol: "questionmark.bubble", title: String(localized: "Ask, or tell it what to do"),
                              text: String(localized: "Start the search field with ? to ask your mail a question, or with ! to say what you want done. A ! shows you what it would do, and nothing happens until you confirm.")))
        }
        steps.append(Step(id: "screener", symbol: "person.crop.circle.badge.questionmark", title: String(localized: "The Screener"),
                          text: String(localized: "Someone new has written? They wait in the Screener for you to decide. Allow keeps them, and Screen out stops their mail reaching your Inbox.")))
        return steps
    }

    /// Offered once, after the first account has mail and with nothing else in the way (`guideOffers`).
    static func offers(preferences: API.Preferences?, accounts: Int, hasMail: Bool, busy: Bool) -> Bool {
        guard let preferences else { return false }
        return !preferences.guideOffered && accounts > 0 && hasMail && !busy
    }
}

/// The guide as a sheet: a step at a time, Back, Skip and Next, Done at the end.
struct GuideSheet: View {
    let steps: [Guide.Step]
    let end: () -> Void
    @State private var at = 0

    var body: some View {
        let step = steps[min(at, steps.count - 1)]
        let last = at >= steps.count - 1
        VStack(alignment: .leading, spacing: 14) {
            Text("\(at + 1) of \(steps.count)")
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: step.symbol)
                    .font(.system(size: 28))
                    .foregroundStyle(.tint)
                    .frame(width: 40)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 8) {
                    Text(step.title).font(.title2.weight(.semibold))
                    Text(step.text).fixedSize(horizontal: false, vertical: true)
                }
            }
            .accessibilityElement(children: .combine)
            Spacer(minLength: 0)
            HStack {
                if at > 0 { Button("Back") { at -= 1 } }
                Spacer()
                if !last { Button("Skip", action: end).keyboardShortcut(.cancelAction) }
                Button(last ? "Done" : "Next") { last ? end() : (at += 1) }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(22)
        .frame(width: 420, height: 230)
    }
}
#endif
