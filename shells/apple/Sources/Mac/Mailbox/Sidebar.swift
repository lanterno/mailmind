#if os(macOS)
import SwiftUI

/// The sidebar, as the page's rail lists places (app/FolderRail.tsx), with every account at once as a Mac's mail
/// sidebar has them: All Inboxes and the places over every account, the views, then each account's folders with
/// their unread counts, its own virtual places and a Gmail account's labels. Every row is a `Place`.
struct MailboxSidebar: View {
    @Bindable var app: MailApp
    let mailbox: Mailbox
    /// The accounts folded away, by address.
    @State private var folded: Set<String> = []

    var body: some View {
        List(selection: $app.place) {
            Section {
                Label("All Inboxes", systemImage: "tray.2")
                    .badge(inboxesUnseen)
                    .tag(Place.allInboxes)
                if let across = mailbox.across {
                    virtualRows(of: nil, counts: across.muted, across.replylater, across.snoozed, across.waiting)
                }
                Label("Documents", systemImage: "doc.on.doc")
                    .tag(Place.documents)
                // Today's brief, and each account's Screener while senders wait in it (Mac/Assistant).
                DeskSidebar()
            }
            if !mailbox.views.isEmpty {
                Section("Views") {
                    ForEach(mailbox.views, id: \.id) { view in
                        viewRow(view)
                    }
                }
            }
            ForEach(mailbox.accounts, id: \.self) { account in
                Section(isExpanded: expanded(account)) {
                    accountRows(account)
                } header: {
                    Text(mailbox.name(of: account))
                        .help(account)
                }
            }
        }
        .listStyle(.sidebar)
        .accessibilityLabel("Accounts and folders")
    }

    /// Every account's Inbox unread, as a Mac's All Inboxes counts it.
    private var inboxesUnseen: Int {
        mailbox.accounts.reduce(0) { sum, account in
            sum + (mailbox.folders(of: account).first { $0.role == "inbox" }?.unseen ?? 0)
        }
    }

    private func expanded(_ account: String) -> Binding<Bool> {
        Binding(get: { !folded.contains(account) }, set: { open in
            if open { folded.remove(account) } else { folded.insert(account) }
        })
    }

    @ViewBuilder private func accountRows(_ account: String) -> some View {
        ForEach(mailbox.folders(of: account), id: \.name) { folder in
            Label(Words.role(folder), systemImage: Self.symbol(folder.role))
                .badge(folder.unseen)
                .tag(Place.folder(account: account, folder: folder.name))
        }
        if let counts = mailbox.folders[account] {
            virtualRows(of: account, counts: counts.muted, counts.replylater, counts.snoozed, counts.waiting)
        }
        let labels = mailbox.labels[account] ?? []
        if !labels.isEmpty {
            DisclosureGroup {
                ForEach(labels, id: \.name) { label in
                    Label(label.name, systemImage: "tag")
                        .badge(label.count)
                        .tag(Place.search(Search(q: labelQuery(label.name), account: account)))
                }
            } label: {
                Label("Labels", systemImage: "tag.fill")
            }
        }
    }

    /// Reply later, Waiting, Snoozed and Muted, each while it holds something, but Snoozed, which a person goes to look in.
    @ViewBuilder private func virtualRows(of account: String?, counts muted: Int, _ replylater: Int, _ snoozed: Int,
                                          _ waiting: Int) -> some View {
        ForEach(VirtualFolder.allCases, id: \.self) { virtual in
            let count = virtual.count(muted: muted, replylater: replylater, snoozed: snoozed, waiting: waiting)
            if virtual.shown(count: count) {
                Label(virtual.title, systemImage: virtual.symbol)
                    .badge(count)
                    .tag(Place.virtual(virtual, account: account))
            }
        }
    }

    /// A saved search, from the account the window is on, as the rail's views search. A person's own can be removed.
    private func viewRow(_ view: API.ListedView) -> some View {
        let lane = Lane(view: view.id)
        let symbol = switch lane {
        case .feed: "book"
        case .papertrail: "receipt"
        case nil: "magnifyingglass"
        }
        return Label(view.name, systemImage: symbol)
            .tag(Place.search(Search(q: viewQuery(view), account: mailbox.searchAccount, lane: lane)))
            .contextMenu {
                if !view.builtin {
                    Button("Remove View", role: .destructive) { mailbox.removeView(view) }
                }
            }
    }

    /// A folder's symbol by the part it plays, which the server names (`FOLDER_ICON`).
    static func symbol(_ role: String) -> String {
        switch role {
        case "inbox": "tray"
        case "sent": "paperplane"
        case "drafts": "doc"
        case "archive": "archivebox"
        case "all": "tray.full"
        case "trash": "trash"
        case "junk", "spam": "xmark.bin"
        default: "folder"
        }
    }
}
#endif
