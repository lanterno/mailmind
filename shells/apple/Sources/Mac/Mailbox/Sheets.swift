#if os(macOS)
import SwiftUI

/// A box to type in over a list of things to pick, found by typing (`Entries` in app/Palette.tsx), drawn as Meadow's
/// palette (`.palette`): the command palette (⌘K), and the folder a move goes to. ↑ and ↓ pick, Return runs, Esc
/// closes. A query that starts with one of its modes' characters (`?` asks, `!` acts) shows that mode's card in the
/// list's place, and Return goes to the mode.
struct Entries: View {
    struct Entry: Identifiable {
        let id: String
        /// The heading it goes under while nothing is typed; "" for none.
        var group = ""
        let label: String
        /// A quieter line under the words.
        var detail: String?
        var hint: String?
        var icon: MeadowIcon?
        let run: @MainActor () -> Void
    }

    /// What the palette does with a query that starts with `prefix`: the card in the list's place for the words after
    /// it, and what Return does with them. Each is handed the way to close the palette.
    struct Mode {
        let prefix: Character
        let card: @MainActor (_ words: String, _ close: @escaping @MainActor () -> Void) -> AnyView
        let submit: @MainActor (_ words: String, _ close: @escaping @MainActor () -> Void) -> Void
    }

    let entries: [Entry]
    /// What leads while nothing is typed (the palette's "Try"), each filling the field with its words.
    var starters: [Entry] = []
    /// What follows the matches once something is typed: a search for it.
    var typed: ((String) -> Entry?)?
    var modes: [Mode] = []
    let placeholder: String
    let label: String
    /// What the field holds as it opens.
    var initial = ""
    var hints: [MeadowPaletteFoot.Hint] = Entries.moveHints
    var operators: [String] = []
    let close: @MainActor () -> Void
    @State private var query = ""
    @State private var selection: TextSelection?
    @State private var picked = 0
    @FocusState private var typing: Bool
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowDialogRoom) private var room

    static let moveHints = [
        MeadowPaletteFoot.Hint(keys: ["↑", "↓"], words: String(localized: "move")),
        MeadowPaletteFoot.Hint(keys: ["↵"], words: String(localized: "open")),
    ]

    /// The rows under the field, with their sections, for what is typed (`palette()`'s `paint`).
    static func rows(_ query: String, entries: [Entry], starters: [Entry],
                     typed: ((String) -> Entry?)?) -> [(section: String?, entry: Entry)] {
        let typedWords = query.trimmingCharacters(in: .whitespaces)
        if typedWords.isEmpty {
            var rows: [(section: String?, entry: Entry)] = starters.enumerated().map {
                ($0.offset == 0 ? String(localized: "Try") : nil, $0.element)
            }
            for (index, entry) in entries.enumerated() {
                let heading = !entry.group.isEmpty && (index == 0 || entries[index - 1].group != entry.group)
                rows.append((heading ? entry.group : nil, entry))
            }
            return rows
        }
        let ranked = Fuzzy.rank(typedWords, entries, label: \.label)
        var rows: [(section: String?, entry: Entry)] = ranked.enumerated().map {
            ($0.offset == 0 ? String(localized: "Commands") : nil, $0.element)
        }
        // What is typed is a search for it, headed Mail, unless the entry names its own heading.
        if let search = typed?(typedWords) {
            rows.append((rows.isEmpty ? nil : search.group.isEmpty ? String(localized: "Mail") : search.group, search))
        }
        return rows
    }

    /// The mode a query is in, and its words after the mode's character.
    static func mode(of query: String, in modes: [Mode]) -> (mode: Mode, words: String)? {
        let trimmed = query.trimmingCharacters(in: .whitespaces)
        guard let first = trimmed.first, let mode = modes.first(where: { $0.prefix == first }) else { return nil }
        return (mode, String(trimmed.dropFirst()).trimmingCharacters(in: .whitespaces))
    }

    var body: some View {
        let moded = Self.mode(of: query, in: modes)
        let rows = moded == nil ? Self.rows(query, entries: entries, starters: starters, typed: typed) : []
        let at = min(picked, max(0, rows.count - 1))
        VStack(spacing: 0) {
            MeadowPaletteField(prompt: placeholder, text: $query, selection: $selection, focus: $typing)
                .onChange(of: query) { picked = 0 }
                .onKeyPress(.downArrow) { step(1, of: rows.count) }
                .onKeyPress(.upArrow) { step(-1, of: rows.count) }
                .onKeyPress(.tab) { .handled }
                .onSubmit {
                    if let moded {
                        moded.mode.submit(moded.words, close)
                    } else {
                        run(rows.indices.contains(at) ? rows[at].entry : nil, starter: at < startersShown(rows))
                    }
                }
                .onExitCommand { close() }
            if let moded {
                MeadowFitScroll(maxHeight: min(470, room * 0.6)) {
                    moded.mode.card(moded.words, close)
                        .padding(8)
                }
            } else {
                list(rows, at: at)
            }
            MeadowPaletteFoot(hints: hints, operators: operators)
        }
        .onAppear {
            query = initial
            typing = true
            // Once the field has the focus, which chooses all it holds: the cursor after it instead.
            Task { @MainActor in selection = TextSelection(insertionPoint: query.endIndex) }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(label)
    }

    private func list(_ rows: [(section: String?, entry: Entry)], at: Int) -> some View {
        ScrollViewReader { scroller in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if rows.isEmpty {
                        Text(Words.matches)
                            .font(.meadow(.ui, 14))
                            .foregroundStyle(meadow.ink3)
                            .padding(.horizontal, 10)
                            .frame(minHeight: 40)
                    }
                    ForEach(Array(rows.enumerated()), id: \.element.entry.id) { index, row in
                        if let section = row.section { MeadowPaletteSection(section) }
                        MeadowPaletteRow(row.entry.label, icon: row.entry.icon, detail: row.entry.detail,
                                         keys: MeadowKeys.caps(row.entry.hint), picked: index == at)
                            .id(row.entry.id)
                            .onTapGesture { run(row.entry, starter: index < startersShown(rows)) }
                            .onHover { if $0 { picked = index } }
                    }
                }
                .padding(8)
            }
            .scrollIndicators(.automatic)
            .frame(height: min(Self.height(of: rows), min(470, room * 0.6)))
            .onChange(of: at) { if rows.indices.contains(at) { scroller.scrollTo(rows[at].entry.id) } }
        }
        .accessibilityLabel("Results")
    }

    /// How tall the rows stand, without laying them out: 40 a row, 48 with a second line, 25 a section's head.
    static func height(of rows: [(section: String?, entry: Entry)]) -> CGFloat {
        guard !rows.isEmpty else { return 56 }
        return rows.reduce(16) { sum, row in
            sum + (row.section == nil ? 0 : 25) + (row.entry.detail?.isEmpty == false ? 48 : 40)
        }
    }

    /// How many of the rows are the starters, which fill the field rather than run.
    private func startersShown(_ rows: [(section: String?, entry: Entry)]) -> Int {
        query.trimmingCharacters(in: .whitespaces).isEmpty ? min(starters.count, rows.count) : 0
    }

    private func step(_ by: Int, of count: Int) -> KeyPress.Result {
        guard count > 0 else { return .ignored }
        picked = (min(picked, count - 1) + by + count) % count
        return .handled
    }

    /// Close first, so a command that opens something gets the focus. A starter fills the field instead.
    private func run(_ entry: Entry?, starter: Bool) {
        guard let entry else { return close() }
        if starter {
            entry.run()
            return
        }
        close()
        Task { @MainActor in entry.run() }
    }
}

/// ⌘K: everything there is to do from here, found by typing (app/Palette.tsx), as the mockup's palette draws it: what
/// to try while nothing is typed, then where to go and what to do with their keys (a place's g then a letter), a
/// search for what is typed, and the window's modes (`?` asks your inbox and `!` acts on many, where the window has
/// an assistant).
struct PaletteSheet: View {
    let mailbox: Mailbox
    var initial = ""
    var modes: [Entries.Mode] = []
    let close: @MainActor () -> Void
    @State private var fill: String?

    var body: some View {
        let ai = !modes.isEmpty
        // The places and the verbs stand under one head, as the mockup's palette has them.
        Entries(entries: mailbox.commands().map {
            Entries.Entry(id: $0.id, group: Self.goAndDo, label: $0.label, hint: $0.hint,
                          icon: PaletteIcons.of($0.id), run: $0.run)
        }, starters: starters(ai: ai), typed: { words in
            Entries.Entry(id: "search-all", label: String(localized: "Search all mail for “\(words)”"),
                          icon: .search) { [mailbox] in mailbox.submit(words) }
        }, modes: modes,
        placeholder: ai ? String(localized: "Search mail, jump anywhere — ? to ask, ! to act")
            : String(localized: "Search mail, jump anywhere"),
        label: String(localized: "Command"), initial: fill ?? initial, hints: Self.hints(ai: ai),
        operators: Self.operators, close: close)
        // A starter's words, put in the field afresh; what it opened on is read only as it opens.
        .id(fill)
    }

    static let operators = ["from:", "has:attachment", "before:", "is:unread"]
    /// The head over every command while nothing is typed.
    static let goAndDo = String(localized: "Go & do")

    static func hints(ai: Bool) -> [MeadowPaletteFoot.Hint] {
        Entries.moveHints + (ai ? [
            MeadowPaletteFoot.Hint(keys: ["?"], words: String(localized: "ask your inbox")),
            MeadowPaletteFoot.Hint(keys: ["!"], words: String(localized: "act on many")),
        ] : [])
    }

    /// What to try (`Try`): asking and acting where the window can, and a search with operators.
    private func starters(ai: Bool) -> [Entries.Entry] {
        var out: [Entries.Entry] = []
        if ai {
            out.append(starter("? " + String(localized: "when is the dentist appointment"), .wind,
                               String(localized: "Ask your inbox — answers cite their sources")))
            out.append(starter("! " + String(localized: "archive newsletters older than a week"), .archive,
                               String(localized: "Act on many — always previewed first")))
        }
        out.append(starter("has:attachment is:unread", .search, String(localized: "Search with operators")))
        return out
    }

    private func starter(_ words: String, _ icon: MeadowIcon, _ detail: String) -> Entries.Entry {
        Entries.Entry(id: "try:\(words)", label: words, detail: detail, icon: icon) { fill = words }
    }
}

/// The palette's icon for each command, by its id, as the mockup's commands each have one.
enum PaletteIcons {
    static func of(_ id: String) -> MeadowIcon {
        if let icon = byId[id] { return icon }
        let parts = id.split(separator: ":", omittingEmptySubsequences: false).map(String.init)
        switch parts.first {
        case "across": return VirtualFolder(rawValue: parts.dropFirst().joined(separator: ":")).map(icon) ?? .folder
        case "acct": return .inbox
        case "fold":
            let folder = parts.dropFirst(2).joined(separator: ":")
            if let virtual = VirtualFolder(rawValue: folder) { return icon(virtual) }
            return folder.uppercased() == "INBOX" ? .inbox : .folder
        case "view": return .search
        default: return .arrowRight
        }
    }

    static func icon(_ virtual: VirtualFolder) -> MeadowIcon {
        switch virtual {
        case .snoozed: .clock
        case .waiting: .hourglass
        case .replyLater: .bookmark
        case .muted: .bellOff
        }
    }

    private static let byId: [String: MeadowIcon] = [
        "all-inboxes": .inbox, "docs": .clip,
        "move-down": .down, "move-up": .up, "next-unread": .down, "prev-unread": .up,
        "archive": .archive, "trash": .trash, "junk": .ban, "move-to": .folder, "star": .star, "unread": .mail,
        "undo": .undo, "grouping": .list, "save-view": .bookmark,
        // The desk's, attention's, the composer's and the status's.
        "snooze": .clock, "mute": .bellOff, "later": .bookmark, "dismiss": .checkCircle, "summarize": .wind,
        "extract": .calAdd, "brief": .sun, "ask": .wind, "do": .wind, "commitments": .todo, "week": .cal,
        "curate": .sliders, "cleanup": .ban, "new-filter": .filter,
        "reply": .reply, "reply-all": .replyAll, "forward": .forward, "compose": .pen,
        "help": .keyboard, "settings": .gear, "sync": .refresh, "reconcile": .restore,
    ]
}

/// Move what is chosen or open to a folder picked by typing (app/MoveTo.tsx), in the palette's dialog: only the
/// account's own folders that take filing, as the server says of each, and not the one the mail is in. New Folder…
/// comes last, and once a name is typed, a new folder by that name: either asks its name, makes it and moves there.
struct MoveToSheet: View {
    let mailbox: Mailbox
    let ask: MoveAsk
    /// What a folder picked moves: the mail window's choice, as the mailbox moves it, unless a message's own window
    /// moves its message. Only the mail window makes a folder to move into, as its folder dialog asks the name.
    var moves: (@MainActor (API.RailFolder) -> Void)?
    let close: @MainActor () -> Void

    var body: some View {
        let ask = ask, mailbox = mailbox, moves = moves
        let making = moves == nil ? [Entries.Entry(id: "\u{0}new", label: String(localized: "New Folder…"), icon: .plus) {
            mailbox.folderAsk = .create(ask, name: "")
        }] : []
        Entries(entries: mailbox.destinations(ask).map { folder in
            Entries.Entry(id: folder.name, label: Words.role(folder), icon: .folder) {
                if let moves { moves(folder) } else { mailbox.move(to: folder) }
            }
        } + making, typed: moves != nil ? nil : { words in
            Entries.Entry(id: "\u{0}new-named", group: String(localized: "New folder"),
                          label: String(localized: "Make “\(words)” and move there"), icon: .plus) {
                mailbox.folderAsk = .create(ask, name: words)
            }
        }, placeholder: String(localized: "Move to a folder…"), label: String(localized: "Folder"),
        hints: [MeadowPaletteFoot.Hint(keys: ["↑", "↓"], words: String(localized: "move")),
                MeadowPaletteFoot.Hint(keys: ["↵"], words: String(localized: "move there"))],
        close: close)
    }
}

/// The name a search is saved under as a view (`saveSearch`), in Meadow's dialog: the query's first 40 characters to
/// start with.
struct SaveViewSheet: View {
    let mailbox: Mailbox
    let query: String
    let close: @MainActor () -> Void
    @State private var name = ""
    @FocusState private var naming: Bool
    @Environment(\.meadow) private var meadow

    var body: some View {
        let empty = name.trimmingCharacters(in: .whitespaces).isEmpty
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text("Save this search as a view")
                    .font(.meadow(.display, 18, .bold))
                    .foregroundStyle(meadow.ink)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                Button(action: close) { MeadowIconView(.x) }
                    .meadowButton(.quiet, icon: true)
                    .accessibilityLabel("Close")
            }
            .padding(.top, 14)
            .padding(.horizontal, 16)
            .padding(.bottom, 10)
            MeadowFieldRow(label: String(localized: "Search")) {
                Text(verbatim: query)
                    .font(.meadow(.mono, 12.5, .semibold))
                    .foregroundStyle(meadow.ink2)
                    .lineLimit(2)
                    .padding(.vertical, 6)
                    .padding(.horizontal, 9)
                    .background(RoundedRectangle(cornerRadius: 8).fill(meadow.paper3))
                    .padding(.vertical, 4)
                    .textSelection(.enabled)
            }
            MeadowFieldRow(label: String(localized: "Name")) {
                TextField("", text: $name)
                    .textFieldStyle(.plain)
                    .font(.meadow(.ui, 14.5))
                    .foregroundStyle(meadow.ink)
                    .focused($naming)
                    .accessibilityLabel("Name")
                    .onSubmit(save)
                    .onExitCommand { close() }
            }
            HStack(spacing: 8) {
                Spacer()
                Button("Cancel", action: close)
                    .meadowButton(.quiet)
                Button("Save View", action: save)
                    .meadowButton(.primary)
                    .disabled(empty)
            }
            .padding(.vertical, 12)
            .padding(.horizontal, 16)
            .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
        }
        .onAppear {
            name = String(query.prefix(40))
            naming = true
        }
    }

    private func save() {
        guard !name.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        mailbox.saveView(named: name, query: query)
        close()
    }
}

/// A labelled row of a dialog's form (`.fld`): the label in 72 points, what it names beside it, over a hairline.
struct MeadowFieldRow<Field: View>: View {
    let label: String
    @ViewBuilder let field: () -> Field
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 8) {
            Text(label)
                .font(.meadow(.ui, 13, .bold))
                .foregroundStyle(meadow.ink3)
                .frame(width: 72, alignment: .leading)
            field()
                .frame(maxWidth: .infinity, minHeight: 34, alignment: .leading)
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 18)
        .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
    }
}
#endif
