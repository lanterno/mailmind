#if os(macOS)
import SwiftUI

// The aside beside (or above) the message (#44's `.paside`): the desk's cards (the summary, what the message
// proposes), then cards of facts the reader already has: a receipt's paper trail, and, where the aside stands beside
// the message, who the sender is and the message's files.

/// What the aside's cards say.
enum AsideWords {
    /// A receipt's amount, as Paper Trail reads one out of its subject and snippet; nil for any other mail.
    static func receipt(_ row: API.FolderRow?, lane: Lane?) -> String? {
        guard let row, row.bucket == "receipts/transactional" || lane == .papertrail,
              let amount = Ledger.amount(subject: row.subject, snippet: row.snippet) else { return nil }
        return Ledger.money(amount.currency, Ledger.isRefund(row.subject) ? -amount.value : amount.value)
    }

    /// What MailMind knows of a sender: how much mail, since when, and how often you answered.
    static func relation(_ facts: API.SenderFacts?) -> String? {
        guard let facts, facts.count > 0 else { return nil }
        var words = facts.count == 1 ? String(localized: "This is the first message from them.")
            : String(localized: "\(facts.count) messages")
        if facts.count > 1 {
            if let first = facts.firstAgo, !first.isEmpty { words += String(localized: ", the first \(first) ago") }
            words += "."
        }
        switch facts.replied {
        case 0: break
        case 1: words += String(localized: " You replied once.")
        default: words += String(localized: " You replied \(facts.replied) times.")
        }
        return words
    }
}

/// The document's aside: the desk's cards, then the reader's own.
struct DocumentAside: View {
    let context: ReaderContext
    /// The message whose sender and files the cards tell of: the open one, or a conversation's latest once loaded.
    let opened: API.MessageOpened?
    /// Where the message is filed, as its chip names it.
    let filed: String
    var lane: Lane?
    /// Write to the sender, and see all their mail: the mailbox's, when the window has one.
    var write: ((String) -> Void)?
    var allMail: ((String) -> Void)?
    @Environment(\.meadowPaneWidth) private var width

    private var wide: Bool { width >= PaneLayout.twoColumnsFrom }

    var body: some View {
        ReaderCards(placement: .top, context: context)
        if let amount = AsideWords.receipt(context.row, lane: lane) {
            SideCard(title: String(localized: "Paper trail"), icon: .receipt) {
                MeadowFact(String(localized: "Amount"), value: amount)
                let from = context.row?.fromName.flatMap { $0.isEmpty ? nil : $0 } ?? context.row?.fromAddr
                MeadowFact(String(localized: "From"), value: from ?? "")
                MeadowFact(String(localized: "Filed under"), value: filed, last: true)
            }
        }
        if wide, let opened {
            if let address = opened.fromAddr, !address.isEmpty, let about = AsideWords.relation(opened.sender) {
                AboutSender(name: opened.fromName.flatMap { $0.isEmpty ? nil : $0 } ?? address, address: address,
                            about: about, write: write, allMail: allMail)
            }
            let files = AttachmentsRow.shown(opened.attachments)
            if !files.isEmpty {
                SideCard(title: String(localized: "Attachments"), icon: .clip) {
                    ForEach(Array(files.enumerated()), id: \.element.partId) { index, file in
                        // A long name gives way in its middle, keeping its kind at the end in sight.
                        MeadowFact(value: FileSize.words(file.size), last: index == files.count - 1) {
                            Text(verbatim: file.filename)
                                .lineLimit(1)
                                .truncationMode(.middle)
                                .frame(maxWidth: 190, alignment: .leading)
                        }
                    }
                }
            }
        }
    }
}

/// A card in the aside (`.side-card`): raised paper, a small capital heading with its icon.
struct SideCard<Content: View>: View {
    let title: String
    let icon: MeadowIcon
    @ViewBuilder let content: Content
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                MeadowIconView(icon, size: 14)
                Text(title.uppercased())
                    .font(.meadow(.ui, 11, .heavy))
                    .tracking(1.1)
            }
            .foregroundStyle(meadow.ink3)
            .padding(.bottom, 10)
            .accessibilityAddTraits(.isHeader)
            content
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(meadow.paperRaised, in: RoundedRectangle(cornerRadius: 14))
        .overlay { RoundedRectangle(cornerRadius: 14).strokeBorder(meadow.line, lineWidth: 1) }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(title)
    }
}

/// About the sender (`.side-card` with `.who-r`): who they are, what MailMind knows of them, Write and All mail.
private struct AboutSender: View {
    let name: String
    let address: String
    let about: String
    let write: ((String) -> Void)?
    let allMail: ((String) -> Void)?
    @Environment(\.meadow) private var meadow

    private var first: String {
        let first = name.split(separator: " ").first.map(String.init) ?? name
        return first.contains("@") ? String(localized: "the sender") : first
    }

    var body: some View {
        SideCard(title: String(localized: "About \(first)"), icon: .user) {
            HStack(spacing: 10) {
                MeadowAvatar(name: name, size: .large)
                VStack(alignment: .leading, spacing: 1) {
                    Text(verbatim: name)
                        .font(.meadow(.ui, 14, .bold))
                        .foregroundStyle(meadow.ink)
                    Text(verbatim: address)
                        .font(.meadow(.ui, 12.5))
                        .foregroundStyle(meadow.ink3)
                        .truncationMode(.middle)
                }
                .lineLimit(1)
                .textSelection(.enabled)
            }
            .padding(.bottom, 10)
            Text(about)
                .font(.meadow(.ui, 13))
                .lineSpacing(MeadowFace.ui.lineSpacing(size: 13, lineHeight: 1.5))
                .foregroundStyle(meadow.ink2)
                .fixedSize(horizontal: false, vertical: true)
            if write != nil || allMail != nil {
                HStack(spacing: 6) {
                    if let write {
                        Button { write(address) } label: {
                            MeadowIconView(.pen, size: 14)
                            Text("Write")
                        }
                        .meadowButton(small: true)
                    }
                    if let allMail {
                        Button { allMail(address) } label: {
                            MeadowIconView(.search, size: 14)
                            Text("All mail")
                        }
                        .meadowButton(.quiet, small: true)
                        .help("Every message from \(address)")
                    }
                }
                .padding(.top, 10)
            }
        }
    }
}
#endif
