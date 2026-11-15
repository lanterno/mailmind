#if os(macOS)
import SwiftUI

/// Who a sender is (discovery/ContactCard.tsx): how much they've written, whether you answer, the accounts they write
/// to, their latest mail, what the AI can say of them on a click, and two ways on: write to them, or their mail.
///
/// It is drawn as Meadow's side card about a person (the reader's "About" card): raised paper with its grain, the
/// avatar beside the name and address, the facts on dashed lines, the AI's words in moss, the actions at its foot.
struct ContactCard: View {
    let mailbox: Mailbox
    let address: String
    @Environment(\.dismiss) private var dismiss
    @Environment(\.meadow) private var meadow
    @State private var card: API.ContactCard?
    @State private var failed = false

    init(mailbox: Mailbox, address: String, card: API.ContactCard? = nil) {
        self.mailbox = mailbox
        self.address = address
        _card = State(initialValue: card)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            DeskCardHeading(title: Self.heading(card), icon: .user)
            if let card {
                who(card)
                facts(card)
                if card.accounts.count > 1 { accounts(card) }
                if !card.recent.isEmpty { recent(card) }
                SenderSummary(mailbox: mailbox, address: card.addr)
                acts(card)
            } else if failed {
                Text("Couldn’t load this sender.")
                    .font(.meadow(.ui, 14))
                    .foregroundStyle(meadow.ink2)
                HStack {
                    Spacer()
                    Button("Done") { dismiss() }
                        .meadowButton(.primary, small: true)
                        .keyboardShortcut(.defaultAction)
                }
            } else {
                ProgressView("Loading…")
                    .controlSize(.small)
                    .font(.meadow(.ui, 13))
                    .foregroundStyle(meadow.ink3)
                    .frame(maxWidth: .infinity, minHeight: 120)
            }
        }
        .padding(20)
        .frame(width: 420, alignment: .leading)
        .background { paper }
        .presentationBackground { paper }
        .task(id: address) { await read() }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("About this sender")
    }

    private var paper: some View {
        ZStack {
            meadow.paperRaised
            MeadowGrain()
        }
    }

    /// The card's name: "About" and the sender's first name, as the reader's card says it.
    static func heading(_ card: API.ContactCard?) -> String {
        let first = card.map { $0.name.split(separator: " ").first.map(String.init) ?? "" } ?? ""
        let said = first.contains("@") ? "" : first
        return said.isEmpty ? String(localized: "About this sender") : String(localized: "About \(said)")
    }

    /// The avatar and who it is (`.who-r`).
    private func who(_ card: API.ContactCard) -> some View {
        HStack(spacing: 12) {
            MeadowAvatar(name: card.name.isEmpty ? card.addr : card.name, size: .large)
            VStack(alignment: .leading, spacing: 2) {
                Text(card.name.isEmpty ? card.addr : card.name)
                    .font(.meadow(.ui, 15, .heavy))
                    .foregroundStyle(meadow.ink)
                    .lineLimit(1)
                Text(card.addr)
                    .font(.meadow(.ui, 12.5))
                    .foregroundStyle(meadow.ink3)
                    .lineLimit(1)
                    .textSelection(.enabled)
            }
        }
    }

    private func facts(_ card: API.ContactCard) -> some View {
        VStack(spacing: 0) {
            MeadowFact(String(localized: "Emails")) { Text(verbatim: "\(card.count)") }
            MeadowFact(String(localized: "You replied"), last: card.firstAgo == nil && card.lastAgo == nil) {
                Text(verbatim: "\(card.replied)×")
            }
            if let first = card.firstAgo {
                MeadowFact(String(localized: "First"), last: card.lastAgo == nil) { Text("\(first) ago") }
            }
            if let last = card.lastAgo {
                MeadowFact(String(localized: "Latest"), last: true) { Text("\(last) ago") }
            }
        }
        // VoiceOver hears the facts as one sentence, as the page's card says them.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel([Words.sender(count: card.count, replied: card.replied, firstAgo: card.firstAgo),
                             card.lastAgo.map { "last \($0) ago" } ?? ""]
            .filter { !$0.isEmpty }.joined(separator: " · "))
    }

    /// The accounts they write to, each in its own colour, as the rows' chips show them.
    private func accounts(_ card: API.ContactCard) -> some View {
        HStack(spacing: 6) {
            Text("Writes to")
                .font(.meadow(.ui, 13))
                .foregroundStyle(meadow.ink3)
            ForEach(card.accounts, id: \.self) { account in
                MeadowChip(mailbox.name(of: account),
                           tone: .account(meadow.account(mailbox.accounts.firstIndex(of: account) ?? 0)))
            }
        }
        .accessibilityElement(children: .combine)
    }

    /// Their latest mail; one opens it in the reader.
    private func recent(_ card: API.ContactCard) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            MeadowSectionLabel(String(localized: "Latest mail"))
                .padding(.bottom, 2)
            ForEach(card.recent, id: \.self) { mail in
                ContactMailRow(mail: mail) {
                    dismiss()
                    mailbox.chose([])
                    mailbox.app?.open(MailCopy(key: mail.key, folder: mail.folder, uid: mail.uid,
                                               account: mail.account))
                }
            }
        }
    }

    /// The card's foot (`.acts`): write to them, their mail, and Done.
    private func acts(_ card: API.ContactCard) -> some View {
        HStack(spacing: 6) {
            if let compose = mailbox.composeTo {
                Button {
                    dismiss()
                    compose(card.addr)
                } label: {
                    MeadowIconView(.pen, size: 14)
                    Text("Write")
                }
                .meadowButton(small: true)
                .help("Write to this sender")
            }
            Button {
                dismiss()
                mailbox.app?.place = .search(Search(q: "from:\(card.addr)"))
            } label: {
                MeadowIconView(.search, size: 14)
                Text("All Mail From Them")
            }
            .meadowButton(.quiet, small: true)
            Spacer(minLength: 0)
            Button("Done") { dismiss() }
                .meadowButton(.primary, small: true)
                .keyboardShortcut(.defaultAction)
        }
        .padding(.top, 2)
    }

    private func read() async {
        if card != nil { return }
        guard let calls = mailbox.calls else { return }
        do {
            card = try await calls.get(API.Get.Contact.self, ["addr": address])
        } catch {
            failed = true
        }
    }
}

/// One of a sender's latest messages: its subject, bold while unread, and when.
private struct ContactMailRow: View {
    let mail: API.RecentMail
    let open: () -> Void
    @Environment(\.meadow) private var meadow
    @State private var hovering = false
    @FocusState private var focused: Bool

    var body: some View {
        Button(action: open) {
            HStack(spacing: 10) {
                Text(mail.subject)
                    .font(.meadow(.ui, 13.5, mail.seen ? .regular : .bold))
                    .foregroundStyle(mail.seen ? meadow.ink2 : meadow.ink)
                    .lineLimit(1)
                Spacer(minLength: 8)
                Text(mail.when)
                    .font(.meadow(.ui, 12))
                    .foregroundStyle(meadow.ink3)
                    .monospacedDigit()
            }
            .padding(.horizontal, 8)
            .frame(minHeight: 30)
            .background(RoundedRectangle(cornerRadius: 8).fill(hovering ? meadow.paper3 : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, -8)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 8)
        .onHover { hovering = $0 }
    }
}

/// What the AI can say of a sender's recent threads, on a click and never without one (`Summarize`): it spends.
/// Its words are the helper's, so they are in moss under the wind mark, never mistakable for the sender's.
private struct SenderSummary: View {
    let mailbox: Mailbox
    let address: String
    @Environment(\.meadow) private var meadow
    @State private var asking = false
    @State private var said: API.SenderSummary?
    @State private var refused: String?
    /// The server said the AI is off, which a person turns on in Settings.
    @State private var off = false

    var body: some View {
        if asking {
            note {
                ProgressView("Reading their recent threads…")
                    .controlSize(.small)
                    .font(.meadow(.ui, 13))
                    .foregroundStyle(meadow.moss)
            }
        } else if let said {
            note {
                Text(said.summary)
                    .font(.meadow(.ui, 13.5))
                    .foregroundStyle(meadow.moss)
                    .lineSpacing(MeadowFace.ui.lineSpacing(size: 13.5, lineHeight: 1.5))
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                if !said.topics.isEmpty {
                    Text(said.topics.joined(separator: " · "))
                        .font(.meadow(.ui, 12.5))
                        .foregroundStyle(meadow.ink3)
                }
                if !said.open.isEmpty {
                    Text("\(Text("Still with you:").bold()) \(said.open)")
                        .font(.meadow(.ui, 13))
                        .foregroundStyle(meadow.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } else if !mailbox.aiOn || off {
            HStack(spacing: 6) {
                MeadowIconView(.wind, size: 14)
                Text("AI is off.")
            }
            .font(.meadow(.ui, 13))
            .foregroundStyle(meadow.ink3)
        } else {
            VStack(alignment: .leading, spacing: 6) {
                if let refused {
                    Text(refused)
                        .font(.meadow(.ui, 13))
                        .foregroundStyle(meadow.ink3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Button { summarize() } label: {
                    MeadowIconView(.wind, size: 14)
                    Text(refused == nil ? "Summarize This Sender" : "Try Again")
                }
                .meadowButton(.ai, small: true)
            }
        }
    }

    /// The helper's words on the moss wash, under its mark (`.note`).
    private func note(@ViewBuilder _ content: () -> some View) -> some View {
        DeskCard(look: .note) {
            DeskCardHeading(title: String(localized: "About them"), icon: .wind, ai: true)
            content()
        }
    }

    private func summarize() {
        guard let calls = mailbox.calls else { return }
        asking = true
        refused = nil
        Task {
            defer { asking = false }
            do {
                said = try await calls.post(API.Post.ContactSummary.self, API.SenderSummaryBody(addr: address),
                                            asking: calls.asksOverLimit)
            } catch let problem as Problem {
                off = problem.more["ai_off"] == .bool(true)
                refused = problem.isRefusal ? problem.detail ?? problem.title
                                            : String(localized: "Couldn’t summarize this sender.")
            } catch {
                refused = String(localized: "Couldn’t summarize this sender.")
            }
        }
    }
}
#endif
