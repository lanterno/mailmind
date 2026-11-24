#if os(macOS)
import SwiftUI

// What comes after a message (#44's `.upnext`), and the pane with nothing open (`.pempty`).

/// Where the open message stands in its list, and what is after it.
struct UpNextPlace: Equatable {
    /// Where the open message is, from 0, and how many the list has.
    let position: Int
    let total: Int

    /// The next one's place, counted from 1: "5 of 13".
    var nextWords: String { String(localized: "\(position + 2) of \(total)") }
    var isLast: Bool { position + 1 >= total }

    /// "End of Inbox · Needs you".
    static func end(title: String, tab: InboxTab?) -> String {
        let place = tab.map { "\(title) · \($0.title)" } ?? title
        return String(localized: "End of \(place)")
    }

    /// The line under the next message's subject: the AI's summary, or the message's own first words where it has
    /// none, or while AI is turned off, when the reader shows no summary either.
    static func gist(of row: API.FolderRow, aiTurnedOff: Bool) -> String {
        aiTurnedOff || row.summary.isEmpty ? row.snippet : row.summary
    }
}

/// Up next (`.upnext`): the message after this one, and File, Delete or Keep, each moving on to it; or, at the end of
/// the list, that this was the last.
struct UpNext: View {
    let app: MailApp
    /// Whether the open message is in Trash, where nothing is filed from here.
    var inTrash = false
    @Environment(\.meadow) private var meadow

    private var mailbox: Mailbox { app.mailbox }

    private var inInbox: Bool {
        switch app.place {
        case .allInboxes?: true
        case .folder(_, let folder)?: folder.caseInsensitiveCompare("INBOX") == .orderedSame
        default: false
        }
    }

    var body: some View {
        // Only where the list is the window's place: a message opened from the brief beside it goes back to the brief.
        if app.place == app.listed, let list = app.list, let position = list.chosenPosition {
            let place = UpNextPlace(position: position, total: max(mailbox.facts.total ?? list.total, list.total))
            VStack(alignment: .leading, spacing: 0) {
                if place.isLast {
                    MeadowSectionLabel(UpNextPlace.end(title: mailbox.title, tab: inInbox ? mailbox.tab : nil))
                        .padding(.bottom, 8)
                    HStack(spacing: 4) {
                        Text("That’s the last one here.")
                            .font(.meadow(.ui, 14))
                            .foregroundStyle(meadow.ink2)
                        if inInbox && mailbox.tab == .needs {
                            Button("Go to Everything") { mailbox.tab = .all }
                                .buttonStyle(ReaderLinkStyle(size: 13))
                                .focusEffectDisabled()
                        }
                    }
                } else {
                    HStack(spacing: 8) {
                        MeadowSectionLabel(String(localized: "Up next"))
                        Spacer(minLength: 8)
                        Text(place.nextWords)
                            .font(.meadow(.ui, 12.5))
                            .foregroundStyle(meadow.ink3)
                    }
                    .padding(.bottom, 8)
                    if let next = list.copy(at: position + 1) {
                        NextRow(row: next, account: mailbox.accounts.firstIndex(of: next.account),
                                mine: RowWords.mine(mailbox.accounts),
                                gist: UpNextPlace.gist(of: next, aiTurnedOff: mailbox.aiTurnedOff)) {
                            mailbox.move(by: 1)
                        }
                    }
                    if !inTrash {
                        FlowLayout(spacing: 8) {
                            Button { mailbox.fileOpen(.archive) } label: {
                                MeadowIconView(.archive, size: 16)
                                Text("File & next")
                            }
                            .meadowButton()
                            Button { mailbox.fileOpen(.trash) } label: {
                                MeadowIconView(.trash, size: 16)
                                Text("Delete & next")
                            }
                            .meadowButton(del: true)
                            Button { mailbox.move(by: 1) } label: {
                                Text("Keep & next")
                                MeadowKey("J")
                            }
                            .meadowButton(.quiet)
                        }
                        .padding(.top, 10)
                    }
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(meadow.paper2, in: RoundedRectangle(cornerRadius: 16))
            .overlay {
                RoundedRectangle(cornerRadius: 16)
                    .strokeBorder(meadow.line2, style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
            }
            .padding(.top, 28)
            .accessibilityElement(children: .contain)
            .accessibilityLabel(place.isLast ? "End of the list" : "Up next")
        }
    }
}

/// The next message's row (`.un-row`): who, what, and its gist.
private struct NextRow: View {
    let row: API.FolderRow
    let account: Int?
    /// Your addresses: your own mail is named by whom it went to, as its row in the list is (`RowWords.who`).
    let mine: Set<String>
    /// The line under its subject (`UpNextPlace.gist`).
    let gist: String
    let open: () -> Void
    @Environment(\.meadow) private var meadow
    @Environment(\.isFocused) private var focused
    @State private var hovering = false

    private var named: RowWords.Who { RowWords.who(row, mine: mine) }
    private var who: String { named.name }

    var body: some View {
        Button(action: open) {
            HStack(spacing: 12) {
                MeadowAvatar(name: named.avatarKey, account: account.map(meadow.account))
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(Text(verbatim: who).fontWeight(.heavy).foregroundStyle(meadow.ink)) — \(row.subject)")
                        .font(.meadow(.ui, 14))
                        .foregroundStyle(meadow.ink)
                        .lineLimit(1)
                    if !gist.isEmpty {
                        Text(gist)
                            .font(.meadow(.ui, 13))
                            .foregroundStyle(meadow.ink2)
                            .lineLimit(1)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                MeadowIconView(.right, size: 16)
                    .foregroundStyle(meadow.ink3)
            }
            .padding(10)
            .background(meadow.paperRaised, in: RoundedRectangle(cornerRadius: 12))
            .overlay {
                RoundedRectangle(cornerRadius: 12).strokeBorder(hovering ? meadow.ink3 : meadow.line, lineWidth: 1)
            }
            .contentShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 12)
        .onHover { hovering = $0 }
        .accessibilityLabel("Next: \(who), \(row.subject)")
    }
}

/// What the pane says with no message open (`.pempty`).
struct PaneEmptyWords: Equatable {
    let title: String
    let line: String
    /// Whether it offers the Inbox's Everything.
    let everything: Bool

    /// With the Inbox's Needs you emptied, with mail to pick from, or with none. With the search field holding the
    /// keyboard, as Return leaves it, J would be typed there: ↓ goes from it to the list's first row. While the AI
    /// reads mail that waits for its summary (`reading`), an emptied Needs you doesn't say that nothing needs you.
    static func of(needsDone: Bool, hasMail: Bool, inSearchField: Bool = false, reading: Bool = false) -> PaneEmptyWords {
        if needsDone {
            return PaneEmptyWords(title: reading ? AIWords.reading : String(localized: "Nothing needs you."),
                                  line: String(localized: "Everything else can wait."), everything: true)
        }
        if hasMail {
            return PaneEmptyWords(title: String(localized: "Pick a message to read"),
                                  line: inSearchField ? String(localized: "Or press ↓ to start at the top.")
                                                      : String(localized: "Or press J to start at the top."),
                                  everything: false)
        }
        return PaneEmptyWords(title: String(localized: "Nothing here."),
                              line: String(localized: "New mail drifts in on its own — no need to refresh."),
                              everything: false)
    }

    /// What the empty pane beside `app`'s list says: rows marked for the bulk bar, the Inbox's Needs you emptied,
    /// mail to pick from, or none.
    @MainActor
    static func of(_ app: MailApp) -> PaneEmptyWords {
        let mailbox = app.mailbox
        let marked = mailbox.chosenRows.count + mailbox.unheld.count
        // Marked whenever the bulk bar shows, one row a check box marked alone among them (`Mailbox.marking`).
        if mailbox.marking {
            return .marked(marked, inTrash: mailbox.chosenRows.first.map { mailbox.inTrash(MailCopy($0)) } ?? false)
        }
        let list = app.list
        let loaded = list?.firstRowsAt != nil
        // Watched, so the words follow the list as it fills or empties, as the page's empty reader does.
        let hasMail = loaded && (list?.watchedTotal ?? 0) > 0
        var inInbox = false
        switch app.place {
        case .allInboxes?: inInbox = true
        case .folder(_, let folder)?: inInbox = folder.caseInsensitiveCompare("INBOX") == .orderedSame
        default: break
        }
        let needsDone = loaded && !hasMail && inInbox && mailbox.tab == .needs
        return of(needsDone: needsDone, hasMail: hasMail, inSearchField: mailbox.inSearchField,
                  reading: mailbox.summarizing)
    }

    /// Several rows marked: how many, as the bulk bar counts them ("4 selected"), and what that bar over the list
    /// does with them, in its own words: File, Delete and Snooze, or in Trash Restore, and Esc to clear them.
    static func marked(_ count: Int, inTrash: Bool) -> PaneEmptyWords {
        PaneEmptyWords(title: String(localized: "\(count) selected"),
                       line: inTrash
                           ? String(localized: "Restore them from the bar over the list, or press Esc to clear the selection.")
                           : String(localized: "File, delete or snooze them from the bar over the list, or press Esc to clear the selection."),
                       everything: false)
    }
}

/// The empty pane: the meadow's picture in a frame, what the pane says, and Everything where it helps.
struct PaneEmpty: View {
    let words: PaneEmptyWords
    var everything: (() -> Void)?
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(spacing: 6) {
            MeadowScene(seed: 3)
                .frame(maxWidth: 560)
                .frame(height: 220)
                .clipShape(RoundedRectangle(cornerRadius: 18))
                .overlay { RoundedRectangle(cornerRadius: 18).strokeBorder(meadow.line, lineWidth: 1) }
                .padding(.bottom, 14)
                .accessibilityHidden(true)
            Text(words.title)
                .font(.meadow(.display, 24, .bold))
                .foregroundStyle(meadow.ink)
                .multilineTextAlignment(.center)
                .accessibilityAddTraits(.isHeader)
            Text(words.line)
                .font(.meadow(.ui, 14))
                .lineSpacing(MeadowFace.ui.lineSpacing(size: 14, lineHeight: 1.55))
                .foregroundStyle(meadow.ink2)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 14 * 0.55 * 42)
                .padding(.top, 2)
                .padding(.bottom, 12)
            if words.everything, let everything {
                Button("See everything", action: everything)
                    .meadowButton()
            }
        }
        .padding(30)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// The pane while a message comes, or when it couldn't: what it says, in the pane's words.
struct PaneNote: View {
    let title: String
    var line: String?
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(spacing: 6) {
            MeadowIconView(.alert, size: 22)
                .foregroundStyle(meadow.rust)
                .padding(.bottom, 4)
            Text(title)
                .font(.meadow(.display, 20, .bold))
                .foregroundStyle(meadow.ink)
                .multilineTextAlignment(.center)
            if let line, !line.isEmpty {
                Text(line)
                    .font(.meadow(.ui, 14))
                    .foregroundStyle(meadow.ink2)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 420)
            }
        }
        .padding(30)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
#endif
