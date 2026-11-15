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

/// The keyboard's legend (the mockup's `keys()`), over the window from the top bar's keys button, `?` and the palette:
/// the window's own keys in groups, and the setting WCAG 2.1.4 asks for, single keys off.
struct KeysLegend: View {
    struct Key: Hashable {
        let keys: [String]
        let words: String

        init(_ keys: String, _ words: String) {
            self.keys = keys.split(separator: " ").map(String.init)
            self.words = words
        }
    }

    struct Group: Hashable {
        let title: String
        let keys: [Key]
    }

    let status: AppStatus
    let close: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool

    static let keysOn = String(localized: """
        Single-letter shortcuts are on. Turn them off if you use voice control or a switch device — ⌘K and \
        modifier keys keep working.
        """)
    static let keysOff = String(localized: """
        Single-letter shortcuts are off. Turn them on to file, reply and move with one key — ⌘K and modifier keys \
        work either way.
        """)

    /// The groups in their three columns: the fourth, Go to, under Write.
    static var columns: [[Group]] {
        [[read], [triage], [write, goTo]]
    }

    static let read = Group(title: String(localized: "Read"), keys: [
        Key("J", String(localized: "Next message")), Key("K", String(localized: "Previous")),
        Key("N", String(localized: "Next unread")), Key("P", String(localized: "Previous unread")),
        Key("↵", String(localized: "Focus reading")), Key("Esc", String(localized: "Leave focus")),
        Key("[", String(localized: "Show/hide mailboxes")), Key("/", String(localized: "Search mail")),
        Key("⌘K", String(localized: "Search, jump, ask")),
    ])

    static let triage = Group(title: String(localized: "Triage"), keys: [
        Key("E", String(localized: "Archive")), Key("D", String(localized: "Delete")),
        Key("#", String(localized: "Delete")), Key("Del", String(localized: "Delete (always on)")),
        Key("B", String(localized: "Junk")), Key("M", String(localized: "Move to…")),
        Key("H", String(localized: "Snooze…")), Key("L", String(localized: "Reply later")),
        Key("W", String(localized: "Mute thread")), Key("S", String(localized: "Star")),
        Key("U", String(localized: "Mark unread")), Key("I", String(localized: "Summarize")),
        Key("V", String(localized: "Save search as view")), Key("Z", String(localized: "Undo")),
    ])

    static let write = Group(title: String(localized: "Write"), keys: [
        Key("R", String(localized: "Reply")), Key("A", String(localized: "Reply all")),
        Key("F", String(localized: "Forward")), Key("C", String(localized: "Compose")),
        Key("⌘↵", String(localized: "Send")), Key("?", String(localized: "This sheet")),
    ])

    /// G then a letter: each folder the mailbox goes to (`Mailbox.roleKeys`, `virtualKeys`), in the page's order.
    static var goTo: Group {
        let roles = "isatkd".compactMap { key in
            Mailbox.roleKeys[key].map { Key("G \(key.uppercased())", $0.prefix(1).uppercased() + $0.dropFirst()) }
        }
        let virtual = "zwl".compactMap { key in
            Mailbox.virtualKeys[key].map { Key("G \(key.uppercased())", $0.title) }
        }
        return Group(title: String(localized: "Go to"), keys: roles + virtual)
    }

    var body: some View {
        let singleKeys = status.preferences?.singleKeys ?? true
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Keyboard")
                    .font(.meadow(.display, 22, .bold))
                    .foregroundStyle(meadow.ink)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                Button(action: close) { MeadowIconView(.x) }
                    .meadowButton(.quiet, icon: true)
                    .accessibilityLabel("Close")
            }
            Text("Every action has a key. Each command in ⌘K shows its key too.")
                .font(.meadow(.ui, 14))
                .foregroundStyle(meadow.ink3)
            HStack(alignment: .top, spacing: 18) {
                ForEach(Array(Self.columns.enumerated()), id: \.offset) { _, groups in
                    VStack(alignment: .leading, spacing: 18) {
                        ForEach(groups, id: \.self) { KeysLegendGroup(group: $0) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(.top, 16)
            MeadowCheckbox(isOn: Binding(get: { singleKeys }, set: { status.setPreferences(.init(singleKeys: $0)) }),
                           focused: $focused) {
                Text(singleKeys ? Self.keysOn : Self.keysOff)
                    .font(.meadow(.ui, 13.5))
                    .foregroundStyle(meadow.ink2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .disabled(status.preferences == nil)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 14)
            .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
            .padding(.top, 18)
        }
        .padding(.vertical, 22)
        .padding(.horizontal, 24)
        .onAppear { focused = true }
        .onExitCommand(perform: close)
        .accessibilityLabel("Keyboard")
    }
}

/// One group of the legend (`.keys h3`, `dl`): its title, then each key beside what it does.
private struct KeysLegendGroup: View {
    let group: KeysLegend.Group
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            MeadowSectionLabel(group.title)
            Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 7) {
                ForEach(group.keys, id: \.self) { key in
                    GridRow {
                        HStack(spacing: 3) {
                            ForEach(Array(key.keys.enumerated()), id: \.offset) { _, cap in MeadowKey(cap) }
                        }
                        Text(key.words)
                            .font(.meadow(.ui, 13.5))
                            .foregroundStyle(meadow.ink2)
                            .lineLimit(1)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
        }
    }
}

/// A checkbox in Meadow's look (`.opt input`): 18 across, indigo with a tick when on, raised paper when off, the
/// focus ring around it while the keys are on it, and its words beside it; Space or a click turns it.
struct MeadowCheckbox<Label: View>: View {
    @Binding var isOn: Bool
    var focused: FocusState<Bool>.Binding
    @ViewBuilder let label: () -> Label
    @Environment(\.meadow) private var meadow
    @Environment(\.isEnabled) private var enabled

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: 4)
                    .fill(isOn ? meadow.primary : meadow.paperRaised)
                RoundedRectangle(cornerRadius: 4)
                    .strokeBorder(isOn ? meadow.primary : meadow.line2, lineWidth: 1.5)
                if isOn {
                    MeadowIconView(.check, size: 14).foregroundStyle(meadow.onPrimary)
                }
            }
            .frame(width: 18, height: 18)
            .meadowFocusRing(focused.wrappedValue, radius: 4)
            label()
        }
        .contentShape(Rectangle())
        .onTapGesture { if enabled { isOn.toggle() } }
        .focusable(enabled)
        .focused(focused)
        .focusEffectDisabled()
        .onKeyPress(.space) {
            isOn.toggle()
            return .handled
        }
        .opacity(enabled ? 1 : 0.5)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isToggle)
        .accessibilityValue(isOn ? Text("On") : Text("Off"))
        .accessibilityAction { isOn.toggle() }
    }
}
#endif
