#if os(macOS)
import Observation
import SwiftUI

/// One message, read as Meadow's document lays it out (#44): its chips and subject, the aside (the summary and the
/// cards), the message, the reply box, and what is up next. The reader scrolls as one, as the page's does.
struct MessageReader: View {
    let core: Core
    let copy: MailCopy
    let opened: API.MessageOpened
    let row: API.FolderRow?
    let prefs: ReadingPrefs
    let mine: Set<String>
    /// The window, for the account's name and colour, the folder's chips and what is up next: none in a test that
    /// draws a reader alone, nor in a message's own window, which names them by `mailbox`.
    var app: MailApp?
    var mailbox: Mailbox?

    var body: some View {
        let context = ReaderContext(core: core, copy: copy, opened: opened, row: row, conversation: nil)
        let place = DocumentPlace.of(app: app, mailbox: mailbox, copy: copy, needsYou: row?.needsYou ?? false,
                                     replied: false)
        PaneDocument(chips: place.chips, subject: opened.subject.flatMap { $0.isEmpty ? nil : $0 } ?? "(no subject)",
                     measure: prefs.measure) {
            DocumentAside(context: context, opened: opened, filed: place.filed, lane: place.lane, write: place.write,
                          allMail: place.allMail, mine: mine)
        } main: {
            MessageArticle(core: core, copy: copy, opened: opened, context: context, prefs: prefs, mine: mine,
                           when: row?.when, account: account(place))
            if opened.isDraft != true, place.offersReply {
                ReplyBox(copy: copy,
                         words: ReplyBoxWords(answering: opened, mine: mine, following: place.role == "sent"),
                         me: place.me,
                         quick: QuickReply.offered(to: opened.fromAddr, mine: mine, isJunk: row?.isJunk ?? false,
                                                   bucket: row?.bucket),
                         keyed: Self.showsReplyKey(app: app, mailbox: mailbox))
            }
            if let app { UpNext(app: app, inTrash: place.role == "trash") }
        }
    }

    @Environment(\.meadow) private var meadow

    /// Whether the reply box shows R, the key that opens it: not in a message's own window (its mailbox, and no mail
    /// window around it), which takes no single keys; Reply there is ⌘R.
    static func showsReplyKey(app: MailApp?, mailbox: Mailbox?) -> Bool {
        app != nil || mailbox == nil
    }

    private func account(_ place: DocumentPlace) -> Color? {
        app == nil && mailbox == nil ? nil : meadow.account(place.accountIndex)
    }
}

/// A conversation, as `/api/thread` answers it: its messages oldest first, the latest open, each one's body loaded
/// once it is opened, and the message a reply answers.
@MainActor
@Observable
final class ConversationModel {
    enum Stage: Equatable {
        case loading
        case open(API.Thread)
        case failed(String)
    }

    let account: String
    let id: String
    private(set) var stage = Stage.loading
    @ObservationIgnored private let routes: any Routes
    /// The profile the digest says MailMind runs with, asked as a member opens: nil until it has said.
    @ObservationIgnored private let profile: @MainActor () -> String?
    /// A member was marked read as it opened: the window's list and counts say so.
    @ObservationIgnored private let marked: @MainActor (MailCopy) -> Void

    init(routes: any Routes, account: String, id: String, profile: @escaping @MainActor () -> String?,
         marked: @escaping @MainActor (MailCopy) -> Void = { _ in })
    {
        self.routes = routes
        self.account = account
        self.id = id
        self.profile = profile
        self.marked = marked
    }

    /// Read the conversation, named as the route names it (`thread_id`), as the page and the mailbox's verbs ask it.
    func load() async {
        do {
            stage = .open(try await routes.get(API.Get.Thread.self, ["account": account, "thread_id": id]))
        } catch {
            stage = .failed(said(error))
        }
    }

    /// A member's body, as `/api/message/body` opens it.
    func body(_ member: API.ThreadMessage) async throws -> API.MessageBodyAnswer {
        try await routes.get(API.Get.MessageBody.self,
                             ["key": String(member.key), "folder": member.folder, "uid": String(member.uid)])
    }

    /// Mark a member read as it opens, if it isn't and the profile lets opening mark it (`MarksOpened`).
    func markRead(_ member: API.ThreadMessage) async {
        guard !member.seen else { return }
        let copy = MailCopy(member, account: account)
        if await MarksOpened.mark(copy, routes: routes, profile: profile()) { marked(copy) }
    }

    /// Each copy once, as the page's `eachCopyOnce` keeps them.
    var members: [API.ThreadMessage] {
        guard case .open(let thread) = stage else { return [] }
        var seen: Set<String> = []
        return thread.items.filter { seen.insert("\($0.key)\n\($0.folder)\n\($0.uid)").inserted }
    }

    /// The member a reply answers: the latest message that isn't yours, as the server names it.
    var replyMember: API.ThreadMessage? {
        guard case .open(let thread) = stage, let key = thread.replyKey else { return nil }
        return members.first { $0.key == key }
    }

    /// What a reply answers, as a copy.
    var replyTo: MailCopy? {
        replyMember.map { MailCopy($0, account: account) }
    }
}

/// Opening a message marks it read on its own, as the page's `markOpenedRead` does, only once the digest has said
/// MailMind runs with its writes on. Read only would turn each one down with its 409, so nothing is asked, and the
/// row stays as the server has it; nor is anything asked before the profile is known.
@MainActor
enum MarksOpened {
    /// Whether opening a message marks it read under `profile`, the digest's: nil until the digest has said.
    static func marks(_ profile: String?) -> Bool {
        guard let profile else { return false }
        return profile != "read-only"
    }

    /// Mark `copy` read as it opens, where `profile` lets it: whether the server took it, so what shows it can say so.
    @discardableResult
    static func mark(_ copy: MailCopy, routes: any OverLimitRoutes, profile: String?) async -> Bool {
        guard marks(profile) else { return false }
        return (try? await routes.post(API.Post.Seen.self, API.SeenBody(folder: copy.folder, key: copy.key, seen: true,
                                                                        uid: copy.uid))) != nil
    }
}

extension MailCopy {
    /// A conversation's member, as a copy.
    init(_ member: API.ThreadMessage, account: String) {
        key = member.key
        folder = member.folder
        uid = member.uid
        self.account = account
    }
}

/// A conversation read whole: its chips and subject, the aside, the earlier messages a line each, the latest open,
/// then the reply box and what is up next.
struct ConversationReader: View {
    let core: Core
    let model: ConversationModel
    let row: API.FolderRow?
    let prefs: ReadingPrefs
    let mine: Set<String>
    var app: MailApp?
    /// The latest message as it opened, which the aside's cards tell of.
    @State private var latest: API.MessageOpened?
    @Environment(\.meadow) private var meadow

    var body: some View {
        switch model.stage {
        case .loading:
            Fetching()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(let why):
            PaneNote(title: String(localized: "Couldn’t load"), line: why)
        case .open(let thread):
            let members = model.members
            if let last = members.last {
                document(thread, members: members, last: last)
            } else {
                PaneNote(title: String(localized: "Nothing here."))
            }
        }
    }

    private func document(_ thread: API.Thread, members: [API.ThreadMessage], last: API.ThreadMessage) -> some View {
        let answers = model.replyMember
        let replyCopy = model.replyTo ?? MailCopy(last, account: model.account)
        let conversation = (id: model.id, account: model.account)
        let context = ReaderContext(core: core, copy: replyCopy, opened: nil, row: row, conversation: conversation)
        let place = DocumentPlace.of(app: app, copy: MailCopy(last, account: model.account),
                                     needsYou: row?.needsYou ?? false, replied: answers?.answered ?? false)
        let subject = row?.subject ?? members.first?.subject ?? ""
        return PaneDocument(chips: place.chips, subject: subject.isEmpty ? "(no subject)" : subject,
                            measure: prefs.measure) {
            DocumentAside(context: context, opened: latest, filed: place.filed, lane: place.lane, write: place.write,
                          allMail: place.allMail, mine: mine)
        } main: {
            if members.count > 1 {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(members.dropLast(), id: \.key) { member in
                        EarlierMessage(core: core, model: model, member: member, row: row, prefs: prefs, mine: mine)
                    }
                }
                .padding(.bottom, 6)
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Earlier in this conversation")
            }
            LatestMessage(core: core, model: model, member: last, row: row, prefs: prefs, mine: mine,
                          colour: app.map { _ in meadow.account(place.accountIndex) }) {
                latest = $0
            }
            .id(MailCopy(last, account: model.account))
            if let answers, place.offersReply {
                ReplyBox(copy: replyCopy,
                         words: ReplyBoxWords(answering: answers, opened: latest, mine: mine,
                                              following: place.role == "sent"),
                         me: place.me, messages: thread.count,
                         quick: QuickReply.offered(to: answers.fromAddr, mine: mine, isJunk: answers.isJunk,
                                                   bucket: row?.bucket))
            }
            if let app { UpNext(app: app, inTrash: place.role == "trash") }
        }
    }
}

/// A conversation's latest message, open: its body loaded, and marked read as it opens.
private struct LatestMessage: View {
    let core: Core
    let model: ConversationModel
    let member: API.ThreadMessage
    let row: API.FolderRow?
    let prefs: ReadingPrefs
    let mine: Set<String>
    let colour: Color?
    let loaded: (API.MessageOpened) -> Void
    @State private var opened: API.MessageOpened?
    @State private var problem: String?
    @Environment(\.meadow) private var meadow

    private var copy: MailCopy { MailCopy(member, account: model.account) }

    var body: some View {
        Group {
            if let opened {
                MessageArticle(core: core, copy: copy, opened: opened,
                               context: ReaderContext(core: core, copy: copy, opened: opened, row: row,
                                                      conversation: (model.id, model.account)),
                               prefs: prefs, mine: mine, when: member.when, account: colour)
            } else if let problem {
                Text(problem).font(.meadow(.ui, 13)).foregroundStyle(meadow.ink3).padding(.top, 16)
            } else {
                Fetching(whole: false)
            }
        }
        .task {
            await model.markRead(member)
            guard opened == nil else { return }
            do {
                switch try await model.body(member) {
                case .messageOpened(let read):
                    opened = read
                    loaded(read)
                case .prefetched:
                    problem = String(localized: "Couldn’t load.")
                }
            } catch {
                problem = said(error)
            }
        }
    }
}
#endif
