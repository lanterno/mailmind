#if os(macOS)
import SwiftUI

/// MailMind Help, from the Help menu (the page's `Help`): where the keys are, the search operators, how MailMind uses
/// AI, the one setting WCAG 2.1.4 asks for (single keys off), and the guide again, written as the mockup's design
/// notes (`.notes`) on Meadow's paper.
struct HelpWindow: View {
    static let id = "mailmind-help"
    static let size = CGSize(width: 620, height: 760)
    /// The band the traffic lights stand in, which moves the window as a title bar does.
    static let barHeight: CGFloat = 40
    let status: AppStatus
    /// Whether the window's traffic lights stand on its paper; a test draws it without a window of its own.
    var lights = true
    @Environment(\.meadow) private var meadow
    @FocusState private var keysFocused: Bool

    /// The page's operators, as its legend lists them.
    static let operators = [
        "from:", "to:", "cc:", "subject:", "body:", "is:unread", "is:flagged", "category:finance", "is:newsletter",
        "is:snoozed", "bucket:receipt", "has:attachment", "in:Sent", "account:", "before:", "after:",
        "\"quoted phrase\"", "-exclude",
    ]

    var body: some View {
        VStack(spacing: 0) {
            Color.clear
                .frame(height: Self.barHeight)
                .background { WindowDragArea() }
            ScrollView {
                notes
                    .padding(.horizontal, 40)
                    .padding(.top, 8)
                    .padding(.bottom, 44)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background {
            ZStack {
                meadow.paper
                MeadowGrain()
            }
        }
        // As Settings' (`SettingsLayout`): the window is sized from the frame below, under the title bar's room, and
        // the paper takes that room too, so it runs to the window's foot.
        .ignoresSafeArea()
        .frame(width: Self.size.width, height: Self.size.height)
        .meadowWindowChrome(barHeight: Self.barHeight, on: lights)
    }

    /// The notes: a kicker over the title (`.hero-date`, `h1`), the lede, then each part under its `h2`.
    private var notes: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Help")
                .font(.meadow(.ui, 12, .heavy))
                .tracking(12 * 0.14)
                .textCase(.uppercase)
                .foregroundStyle(meadow.ink2)
            Text("Finding your way in MailMind")
                .font(.meadow(.display, 34, .bold))
                .foregroundStyle(meadow.ink)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
                .padding(.top, 6)
            Text("Where the keys are, what search understands, and what MailMind sends to an AI service.")
                .font(.meadow(.ui, 17))
                .lineSpacing(MeadowFace.ui.lineSpacing(size: 17, lineHeight: 1.65))
                .foregroundStyle(meadow.ink2)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 12)
            // The guide stands under the lede, where it is seen before the page is scrolled.
            Button("Show Me Around") { status.guiding = true }
                .meadowButton()
                .padding(.top, 16)

            heading("Keyboard")
            paragraph(String(localized: "Every command is in the menu bar, with its keys beside it."))
            MeadowCheckbox(isOn: Binding(get: { !(status.preferences?.singleKeys ?? true) },
                                         set: { status.setPreferences(.init(singleKeys: !$0)) }),
                           focused: $keysFocused) {
                Text("Disable single-key shortcuts (use ⌘ chords only)")
                    .font(.meadow(.ui, 15))
                    .foregroundStyle(meadow.ink)
            }
            .disabled(status.preferences == nil)
            .padding(.top, 12)

            heading("Search operators")
            FlowLayout(spacing: 8) {
                ForEach(Self.operators, id: \.self) { code($0) }
            }
            .textSelection(.enabled)

            aboutAI
                .padding(.top, 40)
        }
    }

    /// A part's heading (`.notes h2`).
    private func heading(_ title: LocalizedStringKey) -> some View {
        Text(title)
            .font(.meadow(.display, 22, .bold))
            .foregroundStyle(meadow.ink)
            .accessibilityAddTraits(.isHeader)
            .padding(.top, 40)
            .padding(.bottom, 12)
    }

    /// The notes' prose: 15 points, lines 1.65 apart.
    private func paragraph(_ words: String) -> some View {
        Text(words)
            .font(.meadow(.ui, 15))
            .lineSpacing(MeadowFace.ui.lineSpacing(size: 15, lineHeight: 1.65))
            .foregroundStyle(meadow.ink)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// An operator as the notes set code (`.notes code`): the mono face on the deepest paper.
    private func code(_ words: String) -> some View {
        Text(verbatim: words)
            .font(.meadow(.mono, 13, .semibold))
            .foregroundStyle(meadow.ink)
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(meadow.paper3, in: RoundedRectangle(cornerRadius: 5))
    }

    /// How MailMind uses AI, set apart as the notes set their revision (`.rev`): the moss wash and its grain.
    private var aboutAI: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 9) {
                MeadowIconView(.wind, size: 18)
                    .foregroundStyle(meadow.moss)
                Text("How MailMind uses AI")
                    .font(.meadow(.display, 19, .bold))
                    .foregroundStyle(meadow.ink)
            }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)
            paragraph(Self.howAI(sends: AIWords.sends(status.preferences, status.digest?.ai)))
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            ZStack {
                meadow.mossWash
                MeadowGrain()
            }
            .clipShape(RoundedRectangle(cornerRadius: 16))
        }
    }

    /// How MailMind uses AI, with `sends`, what AI mode sends to the provider chosen (`AIWords.sends`), said whether
    /// it is on or off.
    static func howAI(sends: String) -> String {
        String(localized: """
            Summaries, importance scores, the daily briefing, drafts, and ask-your-inbox are written by the AI service \
            you choose in Settings › AI: Claude, OpenAI or Gemini with an API key of yours, or a model on this Mac, in \
            a model server such as Ollama or LM Studio. With AI mode off, or without what it needs, AI is off and \
            nothing is sent. \(sends) Your mailbox itself stays in a database on this Mac. Email bodies show inside a \
            locked sandbox that can't run code or phone home.
            """)
    }
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
            // Where they are whatever the rail shows: folded to icons, as it first is, with their names, or in the
            // drawer of a narrow window, each of which the sidebar button gives.
            Step(id: "rail", symbol: "sidebar.left", title: String(localized: "Folders, and your accounts"),
                 text: singleKeys
                    ? String(localized: "Your accounts and folders are down the left. The sidebar button at the top, or [, shows their names or folds them to icons. Open a message and read it beside the list; j and k move through the list.")
                    : String(localized: "Your accounts and folders are down the left. The sidebar button at the top shows their names or folds them to icons. Open a message and read it beside the list.")),
            Step(id: "palette", symbol: "command", title: String(localized: "⌘K opens every command"),
                 text: String(localized: "Folders, filing, settings and more, by name. Press ⌘K any time, and start typing.")),
        ]
        if ai {
            steps.append(Step(id: "ask", symbol: "questionmark.bubble", title: String(localized: "Ask, or tell it what to do"),
                              text: String(localized: "Start the search field with ? to ask your mail a question, or with ! to say what you want done. A ! shows you what it would do, and nothing happens until you confirm.")))
        }
        // Named as the rail and the gate name it, with the gate's own buttons.
        steps.append(Step(id: "screener", symbol: "person.crop.circle.badge.questionmark", title: String(localized: "At the gate"),
                          text: String(localized: "Someone new has written? They wait at the gate for you to decide. Let in keeps them, and Screen out stops their mail reaching your Inbox.")))
        return steps
    }

    /// Offered once, after the first account has mail and with nothing else in the way (`guideOffers`).
    static func offers(preferences: API.Preferences?, accounts: Int, hasMail: Bool, busy: Bool) -> Bool {
        guard let preferences else { return false }
        return !preferences.guideOffered && accounts > 0 && hasMail && !busy
    }
}

/// The guide as a sheet drawn as Meadow's dialog, on its paper in the light's ink: a step at a time, Back, Skip and
/// Next, Done at the end. The arrow keys go back and on as its buttons do.
struct GuideSheet: View {
    let steps: [Guide.Step]
    let end: () -> Void
    @State private var at = 0
    /// The sheet has the keyboard as it opens, so its arrows are its own.
    @FocusState private var focused: Bool
    @Environment(\.meadow) private var meadow

    /// Where an arrow key takes the guide from step `at` of `count`: back a step or on one, staying at either end;
    /// Done, or Return, ends it.
    static func step(from at: Int, of count: Int, forward: Bool) -> Int {
        max(0, min(count - 1, at + (forward ? 1 : -1)))
    }

    private func arrow(forward: Bool) -> KeyPress.Result {
        at = Self.step(from: at, of: steps.count, forward: forward)
        return .handled
    }

    var body: some View {
        let step = steps[min(at, steps.count - 1)]
        let last = at >= steps.count - 1
        VStack(alignment: .leading, spacing: 14) {
            Text("\(at + 1) of \(steps.count)")
                .font(.meadow(.ui, 12, .bold))
                .foregroundStyle(meadow.ink3)
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: step.symbol)
                    .font(.system(size: 26))
                    .foregroundStyle(meadow.primary)
                    .frame(width: 40)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 8) {
                    Text(step.title)
                        .font(.meadow(.display, 20, .bold))
                        .foregroundStyle(meadow.ink)
                    Text(step.text)
                        .font(.meadow(.ui, 14))
                        .foregroundStyle(meadow.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .accessibilityElement(children: .combine)
            Spacer(minLength: 0)
            HStack(spacing: 8) {
                if at > 0 { Button("Back") { at -= 1 }.meadowButton() }
                Spacer()
                if !last { Button("Skip", action: end).meadowButton().keyboardShortcut(.cancelAction) }
                Button(last ? "Done" : "Next") { last ? end() : (at += 1) }
                    .meadowButton(.primary)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(22)
        .frame(width: 440, height: 250)
        .background {
            DialogKeys(focused: $focused) { press in
                switch press.key {
                case .leftArrow: arrow(forward: false)
                case .rightArrow: arrow(forward: true)
                default: .ignored
                }
            }
        }
        .onAppear { focused = true }
        .background { ComposePaper() }
        .presentationBackground { ComposePaper() }
    }
}

/// Where a dialog with no field keeps the keyboard as it opens: it draws nothing, and the keys `press` takes are the
/// dialog's. It stands beside the dialog's buttons, never around them, so none of them rings as if it had the
/// keyboard, and Space presses none of them.
struct DialogKeys: View {
    var focused: FocusState<Bool>.Binding
    let press: (KeyPress) -> KeyPress.Result

    var body: some View {
        Color.clear
            .frame(width: 1, height: 1)
            .focusable()
            .focused(focused)
            .focusEffectDisabled()
            .onKeyPress(phases: .down, action: press)
            .accessibilityHidden(true)
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
    /// The checkbox has the keyboard, by Tab: Space turns it.
    @FocusState private var focused: Bool
    /// The legend itself has the keyboard, as it opens: Space does nothing, and Esc or ? closes it.
    @FocusState private var reading: Bool

    static let keysOn = String(localized: """
        Single-letter shortcuts are on. Turn them off if you use voice control or a switch device — ⌘K and \
        modifier keys keep working.
        """)
    static let keysOff = String(localized: """
        Single-letter shortcuts are off. Turn them on to file, reply and move with one key — ⌘K and modifier keys \
        work either way.
        """)

    /// The groups in their three columns: the fourth, Go to, under Write. With AI turned off in Settings, no key that only
    /// AI serves is listed, as the window offers none.
    static func columns(aiTurnedOff: Bool = false) -> [[Group]] {
        [[read], [aiTurnedOff ? triageWithoutAI : triage], [write, goTo]]
    }

    static let read = Group(title: String(localized: "Read"), keys: [
        Key("J", String(localized: "Next message")), Key("K", String(localized: "Previous")),
        Key("N", String(localized: "Next unread")), Key("P", String(localized: "Previous unread")),
        Key("Space", String(localized: "Page down the message, ⇧ up")),
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
        Key("X", String(localized: "Select")), Key("V", String(localized: "Save search as view")),
        Key("Z", String(localized: "Undo")),
    ])

    /// Triage with AI turned off: Summarize, which only AI serves, left out.
    static let triageWithoutAI = Group(title: triage.title, keys: triage.keys.filter { $0.keys != ["I"] })

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
                ForEach(Array(Self.columns(aiTurnedOff: status.digest?.ai.isTurnedOff ?? false).enumerated()), id: \.offset) { _, groups in
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
        .background {
            DialogKeys(focused: $reading) { press in
                guard SingleKey.typed(press) == "?" || press.key == .escape else { return .ignored }
                close()
                return .handled
            }
        }
        .onAppear {
            reading = true
            // Asked again once the dialog is up: what had the keyboard lets it go only as the dialog comes.
            Task { @MainActor in if !focused { reading = true } }
        }
        .onExitCommand(perform: close)
        // A group VoiceOver reads part by part, each key with its words, under the legend's name.
        .accessibilityElement(children: .contain)
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
                        // A long line wraps under itself, never cut short in its column.
                        Text(key.words)
                            .font(.meadow(.ui, 13.5))
                            .foregroundStyle(meadow.ink2)
                            .fixedSize(horizontal: false, vertical: true)
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
