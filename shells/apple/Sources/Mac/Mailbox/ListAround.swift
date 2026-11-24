#if os(macOS)
import SwiftUI

// What stands around the list's rows (#44), as the mockup draws it: the note while an account can't reach its server
// (`.offline`), the bulk bar while rows are checked (`.bulk`), the rows themselves, and what the list says when it
// has none (`.lempty`).

/// The window's list of mail: the offline note and the bulk bar over the rows, today's brief first in the Inbox, and
/// the empty list's words under it.
struct MailboxTable: View {
    let list: MailListModel
    let mailbox: Mailbox
    /// The rows chosen as the mailbox holds them.
    let chosen: [MailCopy]
    /// Whether each row says its account: everywhere but one account's own folder.
    let showsAccount: Bool
    var status = AppStatus.shared
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowLight) private var light
    @Environment(Desk.self) private var desk: Desk?
    /// The brief's summary fits on one line of the list as wide as it is, as the table's brief row has it.
    @State private var briefOneLine = false

    var body: some View {
        let place = mailbox.app?.place
        let measure = RowMeasure(status.preferences?.density ?? .comfortable)
        let brief = briefLine
        let briefWidth = brief.map { RowFaces.gistWidth($0.gist) }
        VStack(spacing: 0) {
            OfflineNote(mailbox: mailbox, account: mailbox.app?.listed?.account, status: status)
            if mailbox.marking {
                BulkBar(mailbox: mailbox, inTrash: inTrash)
            }
            MailboxRowsTable(list: list, mailbox: mailbox, chosen: chosen,
                             cursor: mailbox.marking ? mailbox.app?.selection.copy : nil, showsAccount: showsAccount,
                             look: RowLook(inks: RowInks.of(meadow, light: light, highContrast: status.highContrast),
                                           measure: measure, accounts: mailbox.accounts,
                                           mine: RowWords.mine(mailbox.accounts, status: status),
                                           places: mailbox.places),
                             brief: brief, briefChosen: place == .desk(.brief),
                             sections: DayGroup.sections(place), day: status.today)
                .overlay(alignment: .top) {
                    EmptyList(app: mailbox.app, mailbox: mailbox, list: list)
                        .padding(.top, brief == nil ? 0
                                    : RowMeasure.listTop + measure.height(RowShape(gist: true, oneLine: briefOneLine)))
                }
                // Only whether it fits is kept, so moving the splitter changes nothing here until that does.
                .onGeometryChange(for: Bool.self) { proxy in
                    briefWidth.map { RowMeasure.fits($0, in: proxy.size.width) } ?? false
                } action: { briefOneLine = $0 }
        }
        // A new day: the brief's line says its date (read as the line is made), and the rows' times are read again,
        // which the core words for the day it is ("Yesterday").
        .onChange(of: status.today) { list.refresh() }
    }

    /// Today's brief over the Inbox's rows (`BriefRow`): in every Inbox, or one account's, while not searching.
    private var briefLine: BriefLine? {
        desk.flatMap { Self.briefLine($0.brief, mailbox: mailbox) }
    }

    /// The brief's line over the Inbox the list shows: what needs you, waits and can wait in that Inbox's mail, one
    /// account's alone in its own, as the rail's Inbox counts what needs you in the scope the window shows. Nil where
    /// the list isn't an Inbox.
    static func briefLine(_ brief: BriefModel, mailbox: Mailbox) -> BriefLine? {
        guard let listed = mailbox.app?.listed, listed.isInbox else { return nil }
        let account = listed.account
        let there = { (item: API.DigestItem) in account == nil || item.account == account }
        let waiting = account.map { mailbox.folders[$0]?.waiting ?? 0 } ?? mailbox.across?.waiting ?? 0
        return BriefLine(needs: brief.needs.filter(there), waiting: waiting,
                         canWait: brief.aiTurnedOff ? nil : brief.tidy.filter(there).count, reading: brief.summarizing)
    }

    /// The rows checked are in Trash, where the bulk bar restores them.
    private var inTrash: Bool {
        guard let first = mailbox.chosenRows.first else { return false }
        return mailbox.folders(of: first.account).first { $0.name == first.folder }?.role == "trash"
    }
}

/// The bulk bar (`.bulk`), while rows are checked (several, or one marked with x or its check box): how many, then
/// filing, deleting and snoozing them, or in Trash restoring them, and clearing the selection.
struct BulkBar: View {
    let mailbox: Mailbox
    let inTrash: Bool
    @Environment(\.meadow) private var meadow

    var body: some View {
        let count = mailbox.chosenRows.count + mailbox.unheld.count
        HStack(spacing: 4) {
            // The count gives way before the buttons' words do.
            Text("\(count) selected")
                .font(.meadow(.ui, 13, .heavy))
                .foregroundStyle(meadow.primaryInk)
                .lineLimit(2)
                .layoutPriority(-1)
            Spacer(minLength: 8)
            if inTrash {
                button(.restore, "Restore") { mailbox.restore() }
            } else {
                button(.archive, "File") { mailbox.file(.archive) }
                button(.trash, "Delete", del: true) { mailbox.file(.trash) }
                button(.clock, "Snooze") { mailbox.snoozeChosen() }
            }
            Button { mailbox.clearMarks() } label: { MeadowIconView(.x, size: 15) }
                .meadowButton(.quiet, small: true, icon: true)
                .help("Clear the selection (Esc)")
                .accessibilityLabel("Clear the selection")
        }
        .padding(EdgeInsets(top: 6, leading: 12, bottom: 6, trailing: 8))
        .background(RoundedRectangle(cornerRadius: 12).fill(meadow.primaryWash))
        .padding(EdgeInsets(top: 8, leading: 8, bottom: 0, trailing: 8))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Bulk actions")
    }

    private func button(_ icon: MeadowIcon, _ title: LocalizedStringKey, del: Bool = false,
                        run: @escaping () -> Void) -> some View {
        Button(action: run) {
            MeadowIconView(icon, size: 15)
            Text(title)
        }
        .meadowButton(.standard, del: del, small: true)
        .fixedSize()
    }
}

/// The note while an account the list shows can't reach its server (`.offline`): work goes on, and the changes made
/// meanwhile wait here and go once it's back; Try again syncs it now.
struct OfflineNote: View {
    let mailbox: Mailbox
    /// The account the list is of, or nil for every account's.
    let account: String?
    let status: AppStatus
    @Environment(\.meadow) private var meadow
    @State private var waiting = 0

    var body: some View {
        let accounts = account.map { [$0] } ?? mailbox.accounts
        let bad = StatusWords.bad(accounts, digest: status.digest, health: status.health)
        if let first = bad.first {
            HStack(alignment: .top, spacing: 10) {
                MeadowIconView(.cloudOff).foregroundStyle(meadow.sunInk).padding(.top, 1)
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(Text(said(bad)).fontWeight(.bold).foregroundStyle(meadow.ink)) \(kept)")
                        .font(.meadow(.ui, 13))
                        .lineSpacing(MeadowFace.ui.lineSpacing(size: 13, lineHeight: 1.45))
                        .foregroundStyle(meadow.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("Try again") { Task { for one in bad { await status.retry(one) } } }
                        .buttonStyle(MeadowLinkStyle())
                        .padding(.leading, -4)
                        .help("Sync \(mailbox.name(of: first)) again")
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(EdgeInsets(top: 10, leading: 12, bottom: 10, trailing: 12))
            .background(RoundedRectangle(cornerRadius: 12).fill(meadow.sunWash))
            .padding(EdgeInsets(top: 8, leading: 8, bottom: 0, trailing: 8))
            .accessibilityElement(children: .combine)
            .task(id: bad) { await readWaiting(bad) }
        }
    }

    private func said(_ bad: [String]) -> String {
        bad.count == 1 ? String(localized: "\(mailbox.name(of: bad[0])) can’t reach its server.")
            : String(localized: "\(bad.count) accounts can’t reach their servers.")
    }

    private var kept: String {
        switch waiting {
        case 0: String(localized: "Keep working — what you change is saved here and applies once it’s back.")
        case 1: String(localized: "Keep working — your change is saved here and applies once it’s back.")
        default: String(localized: "Keep working — your \(waiting) changes are saved here and apply once it’s back.")
        }
    }

    /// The changes waiting to reach the accounts that can't connect (`/api/pending`).
    private func readWaiting(_ bad: [String]) async {
        guard let calls = mailbox.calls, let pending = try? await calls.get(API.Get.Pending.self) else { return }
        waiting = pending.items.filter { bad.contains($0.account) && $0.state != "failed" }.count
    }
}

/// A link's button (`.linkbtn`): the primary's ink, bold, underlined.
struct MeadowLinkStyle: ButtonStyle {
    /// In the run of a sentence, with no room of its own at its sides, so the words around it sit as they would by a
    /// word: "in Everything.", not "in Everything ."
    var inline = false
    @Environment(\.meadow) private var meadow

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.meadow(.ui, 12.5, .bold))
            .underline()
            .foregroundStyle(meadow.primaryInk)
            .padding(.horizontal, inline ? 0 : 4)
            .padding(.vertical, 2)
            .opacity(configuration.isPressed ? 0.7 : 1)
            .contentShape(Rectangle())
    }
}

/// What the list says when it has no rows to show, in the page's words (`.lempty`): a line in the display face, and
/// under it what to do.
struct EmptyList: View {
    let app: MailApp?
    let mailbox: Mailbox
    let list: MailListModel
    @Environment(\.meadow) private var meadow

    var body: some View {
        let facts = mailbox.facts
        let place = app?.listed
        let searching = place?.search != nil
        if list.firstRowsAt == nil {
            if let problem = list.problem {
                failed(searching ? "Search failed." : "Couldn’t load messages.", problem)
            } else {
                ProgressView().controlSize(.small).padding(28)
            }
        } else if (facts.total ?? list.total) == 0 {
            if let problem = list.problem {
                failed(searching ? "Search failed." : "Couldn’t load messages.", problem)
            } else if place?.isInbox == true, mailbox.tab != .all, (facts.read ?? 0) > 0 {
                said(mailbox.tab == .needs ? (mailbox.summarizing ? AIWords.reading : Words.needsYou) : Words.unread) {
                    HStack(spacing: 0) {
                        Text("Everything else is in ")
                        Button("Everything") { mailbox.tab = .all }.buttonStyle(MeadowLinkStyle(inline: true))
                        Text(".")
                    }
                }
            } else if facts.syncing {
                said("Reading this folder for the first time…") { ProgressView().controlSize(.small) }
            } else if facts.syncFailed {
                said("Couldn’t sync this folder.") {
                    Button("Retry") { list.refresh() }.buttonStyle(MeadowLinkStyle())
                }
            } else {
                said(searching ? Words.matches : Words.list) { EmptyView() }
            }
        }
    }

    private func said(_ title: String, @ViewBuilder then: () -> some View) -> some View {
        VStack(spacing: 6) {
            Text(title)
                .font(.meadow(.display, 18, .bold))
                .lineSpacing(MeadowFace.display.lineSpacing(size: 18, lineHeight: 1.3))
                .foregroundStyle(meadow.ink)
            then()
                .font(.meadow(.ui, 14))
                .foregroundStyle(meadow.ink2)
        }
        .multilineTextAlignment(.center)
        .padding(EdgeInsets(top: 28, leading: 18, bottom: 28, trailing: 18))
        .frame(maxWidth: .infinity)
    }

    private func failed(_ title: String, _ why: String) -> some View {
        said(title) {
            VStack(spacing: 6) {
                Text(why)
                Button("Retry") { list.refresh() }.buttonStyle(MeadowLinkStyle())
            }
        }
    }
}
#endif
