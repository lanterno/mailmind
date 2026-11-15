#if os(macOS)
import Observation
import SwiftUI

/// One message, read: its head, the cards, its content and frame, and the quick replies when it is from a person
/// you could answer. The reader scrolls as one, as the page's does, its column as wide as the reading settings say.
struct MessageReader: View {
    let core: Core
    let copy: MailCopy
    let opened: API.MessageOpened
    let row: API.FolderRow?
    let prefs: ReadingPrefs
    let mine: Set<String>

    var body: some View {
        let context = ReaderContext(core: core, copy: copy, opened: opened, row: row, conversation: nil)
        ReaderColumn(width: prefs.width) {
            MessageHeader(opened: opened)
            ReaderCards(placement: .top, context: context)
            MessageContent(core: core, copy: copy, opened: opened, context: context, prefs: prefs, mine: mine)
            if opened.isDraft != true,
               QuickReply.offered(to: opened.fromAddr, mine: mine, isJunk: row?.isJunk ?? false, bucket: row?.bucket)
            {
                QuickReplies(copy: copy)
            }
        }
    }
}

/// The reader's column: as wide as the reading measure, centred, scrolling as one.
struct ReaderColumn<Content: View>: View {
    let width: API.ReadWidth
    @ViewBuilder let content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                content
            }
            .frame(maxWidth: MailLook.width(width), alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.vertical, 18)
            .frame(maxWidth: .infinity)
        }
        .background(Color(nsColor: .textBackgroundColor))
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

    init(routes: any Routes, account: String, id: String) {
        self.routes = routes
        self.account = account
        self.id = id
    }

    func load() async {
        do {
            stage = .open(try await routes.get(API.Get.Thread.self, ["account": account, "id": id]))
        } catch {
            stage = .failed(said(error))
        }
    }

    /// Each copy once, as the page's `eachCopyOnce` keeps them.
    var members: [API.ThreadMessage] {
        guard case .open(let thread) = stage else { return [] }
        var seen: Set<String> = []
        return thread.items.filter { seen.insert("\($0.key)\n\($0.folder)\n\($0.uid)").inserted }
    }

    /// What a reply answers: the latest message that isn't yours, as the server names it.
    var replyTo: MailCopy? {
        guard case .open(let thread) = stage, let key = thread.replyKey,
              let latest = members.first(where: { $0.key == key }) else { return nil }
        return MailCopy(latest, account: account)
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

/// A conversation read whole: its subject and count, the cards, then each message, the latest open.
struct ConversationReader: View {
    let core: Core
    let model: ConversationModel
    let row: API.FolderRow?
    let prefs: ReadingPrefs
    let mine: Set<String>

    var body: some View {
        switch model.stage {
        case .loading:
            Fetching()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(let why):
            ContentUnavailableView("Couldn’t load", systemImage: "exclamationmark.triangle", description: Text(why))
        case .open(let thread):
            let members = model.members
            ReaderColumn(width: prefs.width) {
                HStack(alignment: .firstTextBaseline) {
                    Text(members.first.map { $0.subject.isEmpty ? "(no subject)" : $0.subject } ?? "(no subject)")
                        .font(.title2.weight(.semibold))
                        .textSelection(.enabled)
                        .accessibilityAddTraits(.isHeader)
                    Text("\(thread.count) message\(thread.count == 1 ? "" : "s")")
                        .foregroundStyle(.secondary)
                }
                if let latest = model.replyTo ?? members.last.map({ MailCopy($0, account: model.account) }) {
                    ReaderCards(placement: .top, context: ReaderContext(
                        core: core, copy: latest, opened: nil, row: row, conversation: (model.id, model.account)))
                }
                VStack(spacing: 8) {
                    ForEach(Array(members.enumerated()), id: \.element.key) { index, member in
                        ConversationMember(core: core, member: member, account: model.account,
                                           conversation: (model.id, model.account), row: row,
                                           startsOpen: index == members.count - 1, prefs: prefs, mine: mine)
                    }
                }
                if let latest = members.first(where: { $0.key == thread.replyKey }),
                   QuickReply.offered(to: latest.fromAddr, mine: mine, isJunk: latest.isJunk, bucket: row?.bucket)
                {
                    QuickReplies(copy: MailCopy(latest, account: model.account))
                }
            }
        }
    }
}

/// One message of a conversation: its line, which opens and closes it, and its body once opened. Opening one that
/// is unread marks it read.
struct ConversationMember: View {
    let core: Core
    let member: API.ThreadMessage
    let account: String
    let conversation: (id: String, account: String)
    let row: API.FolderRow?
    let startsOpen: Bool
    let prefs: ReadingPrefs
    let mine: Set<String>
    @State private var open: Bool?
    @State private var opened: API.MessageOpened?
    @State private var problem: String?

    private var isOpen: Bool { open ?? startsOpen }
    private var copy: MailCopy { MailCopy(member, account: account) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button {
                open = !isOpen
            } label: {
                head
            }
            .buttonStyle(.plain)
            .accessibilityValue(isOpen ? "Open" : "Closed")
            .accessibilityHint(isOpen ? "Closes this message" : "Opens this message")
            if isOpen {
                // Kept once loaded, so closing and opening again doesn't reload the frame.
                if let opened {
                    MessageContent(core: core, copy: copy, opened: opened,
                                   context: ReaderContext(core: core, copy: copy, opened: opened, row: row,
                                                          conversation: conversation),
                                   minHeight: 300, pad: 24, prefs: prefs, mine: mine)
                } else if let problem {
                    Text(problem).foregroundStyle(.secondary)
                } else {
                    Fetching(whole: false)
                }
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.separator))
        .task(id: isOpen) {
            guard isOpen else { return }
            if !member.seen { await markRead() }
            if opened == nil { await load() }
        }
    }

    private var head: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(member.fromName ?? member.fromAddr ?? "").font(.headline).lineLimit(1)
                if member.flagged {
                    Image(systemName: "star.fill").foregroundStyle(.yellow).accessibilityLabel("Starred")
                }
                if member.hasAttach {
                    Image(systemName: "paperclip").foregroundStyle(.secondary).accessibilityLabel("Has files")
                }
                if member.answered {
                    Image(systemName: "arrowshape.turn.up.left").foregroundStyle(.secondary).accessibilityLabel("Answered")
                }
                Spacer(minLength: 8)
                Text(member.folder).font(.caption).foregroundStyle(.secondary)
                Text(member.when).font(.callout).foregroundStyle(.secondary)
            }
            if !isOpen {
                Text(member.snippet).font(.callout).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .contentShape(Rectangle())
    }

    private func load() async {
        do {
            switch try await core.get(API.Get.MessageBody.self,
                                      ["key": String(member.key), "folder": member.folder, "uid": String(member.uid)]) {
            case .messageOpened(let read): opened = read
            case .prefetched: problem = String(localized: "Couldn’t load.")
            }
        } catch {
            problem = said(error)
        }
    }

    private func markRead() async {
        _ = try? await core.post(API.Post.Seen.self, API.SeenBody(folder: member.folder, key: member.key, seen: true,
                                                                  uid: member.uid))
    }
}
#endif
