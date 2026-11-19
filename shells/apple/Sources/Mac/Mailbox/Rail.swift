#if os(macOS)
import Foundation

// The rail's places in the order the mockup's `renderRail()` stands them (#44), filled with the real places the page's
// rail offers (app/FolderRail.tsx): today's brief, every account's Inbox together, each Screener at the gate, the
// places over every account, the lanes and the Document Hub, a person's views, then each account's folders, its own
// places and its labels under its name. Folded to icons, as the mockup's folded rail stands, the rail keeps the places
// over every account with one gate for every account's Screener, and offers its views and its folders behind a
// button each that opens it.

/// A place the rail offers.
struct RailItem: Equatable {
    /// What the rail says beside a place.
    enum Count: Equatable {
        case none
        /// How many, in the quietest ink; folded away with the names.
        case plain(Int)
        /// How many need you: in sunlight with its dot, and a sun badge while the rail is folded.
        case sun(Int)
        /// A count that stays as a badge while the rail is folded: the senders at the gate.
        case badge(Int)

        init(_ n: Int, _ make: (Int) -> Count = Count.plain) {
            self = n > 0 ? make(n) : .none
        }
    }

    let place: Place
    let icon: MeadowIcon
    let title: String
    var count: Count = .none
    /// A person's own view, which the place's menu removes.
    var removable: API.ListedView?
}

/// One line of the rail: a place, a section's head, an account's name over its folders, the labels' fold, the line
/// between the folded rail's groups, or the folded rail's Views or Folders button.
struct RailEntry: Identifiable, Equatable {
    enum Kind: Equatable {
        case item(RailItem)
        case head(String)
        case account(String)
        case labels(account: String, open: Bool)
        case separator
        case views
        case folders
    }

    /// Whether the entry shows with the names, folded to icons, or both.
    enum Shown: Equatable {
        case always, open, folded
    }

    let id: String
    let kind: Kind
    var shown = Shown.always
    /// The entry that stands for this one while the rail is folded and this one is folded away: the one gate for an
    /// account's, the Views button for a view, the Folders button for the rest.
    var foldsInto = "folders"

    func shows(open: Bool) -> Bool {
        switch shown {
        case .always: true
        case .open: open
        case .folded: !open
        }
    }

    var item: RailItem? {
        if case .item(let item) = kind { return item }
        return nil
    }
}

/// What the desk adds to the rail (Mac/Assistant's `Desk.rail`): today's brief, what needs you, and each account's
/// Screener with the senders waiting in it.
struct DeskRail: Equatable {
    struct Gate: Equatable {
        let account: String
        let waiting: Int
    }

    /// A message the brief says needs you: whose, and the conversation it is in.
    struct Need: Equatable {
        let account: String
        let conversation: String
    }

    var brief = false
    /// The mail the brief says needs you, which the Inbox counts in sunlight.
    var needing: [Need] = []
    /// Every account's Screener, in the accounts' order.
    var gates: [Gate] = []

    /// How many rows of the Inbox's list need you: `account`'s, or every account's with none, each conversation once
    /// where the list shows conversations, as its Needs you tab counts them.
    func needs(in account: String?, grouped: Bool) -> Int {
        let mine = needing.filter { account == nil || $0.account == account }
        guard grouped else { return mine.count }
        return Set(mine.map { [$0.account, $0.conversation] }).count
    }
}

@MainActor
enum RailPlaces {
    /// Every line of the rail, open or folded (each says which it shows in), for the mailbox as it stands. `scope` is
    /// the account whose mail the window shows, nil for every account's (the accounts' button says which); `closed`
    /// holds the accounts whose folders are folded away, and `labelsOpen` those whose labels are shown.
    static func entries(_ mailbox: Mailbox, desk: DeskRail, scope: String? = nil, closed: Set<String> = [],
                        labelsOpen: Set<String> = []) -> [RailEntry] {
        var out: [RailEntry] = []
        func add(_ id: String, _ item: RailItem, _ shown: RailEntry.Shown = .always, foldsInto: String = "folders") {
            out.append(RailEntry(id: id, kind: .item(item), shown: shown, foldsInto: foldsInto))
        }

        if desk.brief {
            add("brief", RailItem(place: .desk(.brief), icon: .sun, title: String(localized: "Today’s brief")))
        }
        // The Inbox of the scope, as the page's rail has it: the account's own while the window shows one, every
        // account's together under All accounts. It counts what needs you there in sunlight, else what is unread.
        let needs = desk.needs(in: scope, grouped: mailbox.grouped)
        add("inbox", RailItem(place: inbox(of: scope, mailbox), icon: .inbox, title: String(localized: "Inbox"),
                              count: needs > 0 ? .sun(needs) : RailItem.Count(inboxesUnseen(mailbox, in: scope))))
        // Open, each Screener senders wait in, named where there are several accounts. Folded, one gate for them all,
        // counting everyone waiting, which opens the first that has someone waiting, else the first account's.
        let waiting = desk.gates.filter { $0.waiting > 0 }
        for gate in waiting {
            let title = desk.gates.count > 1
                ? String(localized: "At the gate · \(mailbox.name(of: gate.account))")
                : String(localized: "At the gate")
            add("gate:\(gate.account)", RailItem(place: .desk(.screener(account: gate.account)), icon: .gate,
                                                 title: title,
                                                 count: RailItem.Count(gate.waiting, RailItem.Count.badge)),
                .open, foldsInto: "gate")
        }
        if let first = waiting.first ?? desk.gates.first {
            add("gate", RailItem(place: .desk(.screener(account: first.account)), icon: .gate,
                                 title: String(localized: "At the gate"),
                                 count: RailItem.Count(waiting.reduce(0) { $0 + $1.waiting }, RailItem.Count.badge)),
                .folded)
        }
        if let across = mailbox.across {
            for item in virtual(of: nil, across.muted, across.replylater, across.snoozed, across.waiting) {
                add("virtual:\(item.place)", item)
            }
        }

        out.append(RailEntry(id: "sep", kind: .separator, shown: .folded))
        out.append(RailEntry(id: "head:lanes", kind: .head(String(localized: "Lanes")), shown: .open))
        let lanes = mailbox.views.filter { Lane(view: $0.id) != nil }
        for view in lanes {
            add("view:\(view.id)", viewItem(view, mailbox))
        }
        add("documents", RailItem(place: .documents, icon: .file, title: String(localized: "Documents")))
        // A person's views, each its own place open and all behind one button folded.
        let views = mailbox.views.filter { Lane(view: $0.id) == nil }
        if !views.isEmpty {
            out.append(RailEntry(id: "head:views", kind: .head(String(localized: "Views")), shown: .open))
            for view in views {
                add("view:\(view.id)", viewItem(view, mailbox), .open, foldsInto: "views")
            }
            out.append(RailEntry(id: "views", kind: .views, shown: .folded))
        }

        if !mailbox.accounts.isEmpty {
            out.append(RailEntry(id: "folders", kind: .folders, shown: .folded))
            out.append(RailEntry(id: "head:folders", kind: .head(String(localized: "Folders")), shown: .open))
        }
        for account in mailbox.accounts {
            out.append(RailEntry(id: "account:\(account)", kind: .account(account), shown: .open))
            guard !closed.contains(account) else { continue }
            for folder in mailbox.folders(of: account) {
                add("folder:\(account)\u{0}\(folder.name)",
                    RailItem(place: .folder(account: account, folder: folder.name), icon: icon(folder.role),
                             title: Words.role(folder), count: RailItem.Count(folder.unseen)), .open)
            }
            if let counts = mailbox.folders[account] {
                for item in virtual(of: account, counts.muted, counts.replylater, counts.snoozed, counts.waiting) {
                    add("virtual:\(item.place)", item, .open)
                }
            }
            let labels = mailbox.labels[account] ?? []
            guard !labels.isEmpty else { continue }
            let shown = labelsOpen.contains(account)
            out.append(RailEntry(id: "labels:\(account)", kind: .labels(account: account, open: shown), shown: .open))
            guard shown else { continue }
            for label in labels {
                add("label:\(account)\u{0}\(label.name)",
                    RailItem(place: .search(Search(q: labelQuery(label.name), account: account)), icon: .tag,
                             title: label.name, count: RailItem.Count(label.count)), .open)
            }
        }
        return out
    }

    /// The places a person moves through with the arrow keys, in the rail's order, as it shows now.
    static func places(_ entries: [RailEntry], open: Bool) -> [Place] {
        entries.filter { $0.shows(open: open) }.compactMap(\.item?.place)
    }

    /// The entry that stands for `place`: its own, or while the rail is folded the one that stands for a place folded
    /// away with the names (the gate, the Views button or the Folders button).
    static func current(_ place: Place?, in entries: [RailEntry], open: Bool) -> String? {
        guard let place else { return nil }
        let own = entries.filter { $0.item?.place == place }
        if let shown = own.first(where: { $0.shows(open: open) }) { return shown.id }
        guard !open, let folded = own.first else { return nil }
        return entries.first { $0.id == folded.foldsInto && $0.shows(open: false) }?.id
    }

    /// Where the arrow keys go from `place`, one place down (`by` 1) or up (-1) the rail as it shows now: from a
    /// place folded away, from the entry that stands for it. Nil past either end.
    static func step(from place: Place?, by: Int, in entries: [RailEntry],
                     open: Bool) -> (place: Place, id: String)? {
        let shown = entries.filter { $0.shows(open: open) }
        let stops = shown.indices.filter { shown[$0].item != nil }
        let at = current(place, in: entries, open: open).flatMap { id in shown.firstIndex { $0.id == id } }
        let next = if let at {
            by > 0 ? stops.first { $0 > at } : stops.last { $0 < at }
        } else {
            by > 0 ? stops.first : stops.last
        }
        guard let next, let item = shown[next].item else { return nil }
        return (item.place, shown[next].id)
    }

    /// The unread in `account`'s Inbox, or in every account's with none, as the server counts each folder's: messages,
    /// as the page's rail counts them.
    static func inboxesUnseen(_ mailbox: Mailbox, in account: String? = nil) -> Int {
        mailbox.accounts.filter { account == nil || $0 == account }.reduce(0) { sum, account in
            sum + (mailbox.folders(of: account).first { $0.role == "inbox" }?.unseen ?? 0)
        }
    }

    /// The Inbox the rail's Inbox opens: `account`'s, or All Inboxes with none.
    static func inbox(of account: String?, _ mailbox: Mailbox) -> Place {
        guard let account else { return .allInboxes }
        let inbox = mailbox.folders(of: account).first { $0.role == "inbox" }?.name
        return .folder(account: account, folder: inbox ?? "INBOX")
    }

    /// The icon for a folder's part, which the server names (`FOLDER_ICON`).
    static func icon(_ role: String) -> MeadowIcon {
        switch role {
        case "inbox": .inbox
        case "sent": .send
        case "drafts": .file
        case "archive", "all": .archive
        case "trash": .trash
        case "junk", "spam": .ban
        default: .folder
        }
    }

    /// Waiting, Reply later, Snoozed and Muted, as the mockup orders them, each while it holds something but Snoozed,
    /// which a person goes to look in.
    private static func virtual(of account: String?, _ muted: Int, _ replylater: Int, _ snoozed: Int,
                                _ waiting: Int) -> [RailItem] {
        [VirtualFolder.waiting, .replyLater, .snoozed, .muted].compactMap { virtual in
            let count = virtual.count(muted: muted, replylater: replylater, snoozed: snoozed, waiting: waiting)
            guard virtual.shown(count: count) else { return nil }
            return RailItem(place: .virtual(virtual, account: account), icon: virtual.icon, title: virtual.title,
                            count: RailItem.Count(count))
        }
    }

    /// A saved search, from the account the window is on, as the page's views search. A person's own can be removed.
    private static func viewItem(_ view: API.ListedView, _ mailbox: Mailbox) -> RailItem {
        let lane = Lane(view: view.id)
        let icon: MeadowIcon = switch lane {
        case .feed: .leaf
        case .papertrail: .receipt
        case nil: .search
        }
        return RailItem(place: .search(Search(q: viewQuery(view), account: mailbox.searchAccount, lane: lane)),
                        icon: icon, title: view.name, removable: view.builtin ? nil : view)
    }
}

extension VirtualFolder {
    /// The mockup's icon for it.
    var icon: MeadowIcon {
        switch self {
        case .replyLater: .bookmark
        case .waiting: .hourglass
        case .snoozed: .clock
        case .muted: .bellOff
        }
    }
}
#endif
