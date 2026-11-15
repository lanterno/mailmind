#if os(macOS)
import SwiftUI

// A message in the pane's document (#44), as the mockup's `.msg` draws it: who it is from, to whom and when, the
// trust line (where it is from and what its frame keeps out), then what `MessageContent` holds: the invite, the body
// and its files. A conversation's earlier messages stand above the latest as one line each, and open in place.

/// What a message's head and trust line say.
enum MessageWords {
    /// Where a mail is from, as the trust line says it: a sender MailMind has mail from before, and how much; one it
    /// has none from; or no sender to say.
    enum From: Equatable {
        case known(domain: String, relation: String)
        case external(domain: String)
        case unknown
    }

    /// "to you" when one of yours is among the To (or none is named), else the first one named; and how many more.
    static func toWhom(_ to: String, mine: Set<String>) -> String {
        let addresses = Addresses.parse(to).map { $0.lowercased() }
        let more = addresses.count > 1 ? String(localized: " and \(addresses.count - 1) more") : ""
        guard let first = addresses.first, !addresses.contains(where: { SendGuard.isMine($0, mine) }) else {
            return String(localized: "to you") + more
        }
        let named = (Addresses.split(to).first ?? "").split(separator: "<").first.map {
            $0.trimmingCharacters(in: CharacterSet.whitespaces.union(CharacterSet(charactersIn: "\"'")))
        } ?? ""
        let who = named.isEmpty || named.contains("@") ? first : named
        return String(localized: "to \(who)") + more
    }

    /// Where `sender` is from, as the server's facts on them have it.
    static func from(_ sender: String?, facts: API.SenderFacts?) -> From {
        guard let sender, let at = sender.lastIndex(of: "@") else { return .unknown }
        let domain = sender[sender.index(after: at)...].lowercased()
        guard !domain.isEmpty else { return .unknown }
        guard let facts, facts.count > 1 else { return .external(domain: domain) }
        let since = facts.firstAgo.flatMap { $0.isEmpty ? nil : String(localized: ", the first \($0) ago") } ?? ""
        return .known(domain: domain, relation: String(localized: "\(facts.count) messages\(since)"))
    }

    /// The trust line's other side: the frame's sandbox, and the trackers it kept out.
    static func sandbox(blocked: Int) -> String {
        blocked > 0 ? String(localized: "Sandboxed · \(blocked) tracker\(blocked == 1 ? "" : "s") blocked")
            : String(localized: "Sandboxed")
    }
}

/// The message's head (`.msg-h`): the sender's avatar with the account's dot, their name, their address and to whom,
/// and when.
struct MessageHead: View {
    let opened: API.MessageOpened
    /// When, as the list says it, else as the message's header does.
    var when: String?
    var account: Color?
    let mine: Set<String>
    @Environment(\.meadow) private var meadow

    private var name: String {
        [opened.fromName, opened.fromAddr].compactMap { $0 }.first { !$0.isEmpty }
            ?? String(localized: "(unknown sender)")
    }

    var body: some View {
        HStack(spacing: 12) {
            MeadowAvatar(name: name, size: .large, account: account)
            VStack(alignment: .leading, spacing: 1) {
                Text(name)
                    .font(.meadow(.ui, 15, .heavy))
                    .foregroundStyle(meadow.ink)
                    .lineLimit(1)
                Text(verbatim: [opened.fromAddr ?? "", MessageWords.toWhom(opened.to, mine: mine)]
                    .filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.meadow(.ui, 13))
                    .foregroundStyle(meadow.ink3)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if !opened.cc.isEmpty {
                    Text("cc \(opened.cc)")
                        .font(.meadow(.ui, 12.5))
                        .foregroundStyle(meadow.ink3)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(verbatim: when.flatMap { $0.isEmpty ? nil : $0 } ?? opened.date)
                .font(.meadow(.ui, 13))
                .foregroundStyle(meadow.ink3)
                .lineLimit(1)
                .fixedSize()
        }
        .accessibilityElement(children: .combine)
    }
}

/// The trust line (`.trust`): where the mail is from, and that its frame is a sandbox with what it kept out, with
/// the images it held back a click away.
struct TrustLine: View {
    let from: MessageWords.From
    let blocked: Int
    /// Load the remote images held back, when there are any.
    var loadImages: (() -> Void)?
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 8) {
            MeadowIconView(.shield, size: 15)
            lead
            Spacer(minLength: 8)
            Text(MessageWords.sandbox(blocked: blocked))
            if let loadImages, blocked > 0 {
                Button("Show images", action: loadImages)
                    .buttonStyle(ReaderLinkStyle(size: 12))
                    .focusEffectDisabled()
                    .help("Loading a remote image can let the sender know you opened this email")
                    .accessibilityHint("Loading one can let the sender know you opened this email")
            }
        }
        .font(.meadow(.ui, 12.5))
        .foregroundStyle(meadow.ink3)
        .lineLimit(1)
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(meadow.paper2, in: RoundedRectangle(cornerRadius: 10))
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var lead: some View {
        switch from {
        case .known(let domain, let relation):
            Text("\(Text(verbatim: domain).fontWeight(.bold).foregroundStyle(meadow.ink2)) · \(relation)")
        case .external(let domain):
            Text("External · \(Text(verbatim: domain).fontWeight(.bold).foregroundStyle(meadow.ink2))")
        case .unknown:
            Text("No sender")
        }
    }
}

/// The trust line's warning (`.trust.warn`): something a reader should know before answering, in rust.
struct TrustWarning: View {
    let text: String
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            MeadowIconView(.alert, size: 15)
                .padding(.top, 2)
            Text(text)
                .lineSpacing(MeadowFace.ui.lineSpacing(size: 12.5, lineHeight: 1.5))
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .font(.meadow(.ui, 12.5, .semibold))
        .foregroundStyle(meadow.rust)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(meadow.rustWash, in: RoundedRectangle(cornerRadius: 10))
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isStaticText)
    }
}

/// One message read in the pane: its head, then its content (`.msg`).
struct MessageArticle: View {
    let core: Core
    let copy: MailCopy
    let opened: API.MessageOpened
    let context: ReaderContext
    let prefs: ReadingPrefs
    let mine: Set<String>
    var when: String?
    var account: Color?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            MessageHead(opened: opened, when: when, account: account, mine: mine)
            MessageContent(core: core, copy: copy, opened: opened, context: context, prefs: prefs, mine: mine)
        }
        .padding(.top, 16)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Message from \(opened.fromName ?? opened.fromAddr ?? "")")
    }
}

/// An earlier message of the conversation (`.earlier`): one line, its sender, what it says and when, which opens in
/// place to the whole message, and closes again.
struct EarlierMessage: View {
    let core: Core
    let model: ConversationModel
    let member: API.ThreadMessage
    let row: API.FolderRow?
    let prefs: ReadingPrefs
    let mine: Set<String>
    @State private var open = false
    @State private var opened: API.MessageOpened?
    @State private var problem: String?
    @Environment(\.meadow) private var meadow

    private var copy: MailCopy { MailCopy(member, account: model.account) }
    private var name: String { member.fromName.flatMap { $0.isEmpty ? nil : $0 } ?? member.fromAddr ?? "" }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { open.toggle() } label: { line }
                .buttonStyle(.plain)
                .focusEffectDisabled()
                .accessibilityValue(open ? "Open" : "Closed")
                .accessibilityHint(open ? "Closes this message" : "Opens this message")
            if open {
                Group {
                    if let opened {
                        MessageContent(core: core, copy: copy, opened: opened,
                                       context: ReaderContext(core: core, copy: copy, opened: opened, row: row,
                                                              conversation: (model.id, model.account)),
                                       prefs: prefs, mine: mine)
                    } else if let problem {
                        Text(problem).font(.meadow(.ui, 13)).foregroundStyle(meadow.ink3).padding(.top, 8)
                    } else {
                        Fetching(whole: false)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.bottom, 12)
            }
        }
        .background(meadow.paperRaised, in: RoundedRectangle(cornerRadius: 12))
        .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(meadow.line, lineWidth: 1) }
        .task(id: open) {
            guard open else { return }
            await model.markRead(member)
            if opened == nil { await load() }
        }
    }

    /// The line (`.earlier button`): avatar, name, what it says, when.
    private var line: some View {
        HStack(spacing: 10) {
            MeadowAvatar(name: name, size: .small)
            Text(name)
                .font(.meadow(.ui, 13, .bold))
                .foregroundStyle(meadow.ink)
                .lineLimit(1)
                .fixedSize()
            Text(open ? "" : member.snippet)
                .font(.meadow(.ui, 13))
                .foregroundStyle(meadow.ink2)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(verbatim: member.when)
                .font(.meadow(.ui, 12))
                .foregroundStyle(meadow.ink3)
                .lineLimit(1)
                .fixedSize()
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .frame(minHeight: 40)
        .contentShape(Rectangle())
    }

    private func load() async {
        do {
            switch try await model.body(member) {
            case .messageOpened(let read): opened = read
            case .prefetched: problem = String(localized: "Couldn’t load.")
            }
        } catch {
            problem = said(error)
        }
    }
}
#endif
