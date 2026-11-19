#if os(macOS)
import SwiftUI

/// One message that needs you (`.need`): who, which account, the next step in sunlight, the subject that opens it,
/// what the AI made of it in moss, and what to do about it: the reply (or the one written ahead), Read, and snooze,
/// file and delete.
struct NeedCard: View {
    let item: API.DigestItem
    let model: BriefModel
    let attention: AttentionModel
    let actions: BriefActions
    @Environment(\.meadow) private var meadow
    @State private var hovering = false

    var body: some View {
        let copy = BriefModel.copy(of: item)
        let subject = item.subject.isEmpty ? String(localized: "(no subject)") : item.subject
        let accounts = model.digest?.accounts ?? []
        // Your own mail is named by whom it went to (`RowWords.who`), as the list's row names it.
        let named = RowWords.who(item, mine: model.mine)
        let who = named.name.isEmpty ? "—" : named.name
        let account = meadow.account(of: item.account, among: accounts)
        let action = item.action.trimmingCharacters(in: .whitespacesAndNewlines)
        HStack(alignment: .top, spacing: 12) {
            MeadowAvatar(name: named.avatarKey.isEmpty ? who : named.avatarKey, account: model.multi ? account : nil)
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text(who)
                        .font(.meadow(.ui, 14, .bold))
                        .foregroundStyle(meadow.ink)
                        .lineLimit(1)
                    if model.multi {
                        MeadowChip(item.alias.flatMap { $0.isEmpty ? nil : $0 } ?? Self.short(item.account),
                                   tone: .account(account))
                    }
                    Spacer(minLength: 4)
                    if action.isEmpty {
                        Text(item.when).font(.meadow(.ui, 12.5)).foregroundStyle(meadow.ink3)
                    } else {
                        MeadowChip(action, tone: .sun)
                            .frame(maxWidth: 240, alignment: .trailing)
                            .help(action)
                    }
                }
                Button(subject) { actions.open(copy) }
                    .deskTitle()
                    .font(.meadow(.ui, 15.5, .bold))
                    .foregroundStyle(meadow.ink)
                    .multilineTextAlignment(.leading)
                    .lineLimit(2)
                    .padding(.top, 4)
                if let summary = item.summary, !summary.isEmpty {
                    Text(summary)
                        .font(.meadow(.ui, 14.5, .medium))
                        .foregroundStyle(meadow.moss)
                        .lineSpacing(MeadowFace.ui.lineSpacing(size: 14.5, lineHeight: 1.45, weight: .medium))
                        .lineLimit(3)
                        .padding(.top, 4)
                        .accessibilityLabel(Text("MailMind summary: \(summary)"))
                }
                footer(copy, subject: subject)
                    .padding(.top, 10)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16).fill(meadow.paperRaised))
        .overlay {
            RoundedRectangle(cornerRadius: 16).strokeBorder(hovering ? meadow.line2 : meadow.line, lineWidth: 1)
        }
        .onHover { hovering = $0 }
        .accessibilityElement(children: .contain)
    }

    private func footer(_ copy: MailCopy, subject: String) -> some View {
        HStack(spacing: 4) {
            if let reply = actions.reply {
                if actions.draftReady(item.key) {
                    Button { reply(copy) } label: {
                        MeadowIconView(.wind, size: 15)
                        Text("Use draft")
                    }
                    .meadowButton(.ai, small: true)
                    .help("Opens the pre-written reply in the composer; nothing sends until you do")
                } else {
                    Button { reply(copy) } label: {
                        MeadowIconView(.reply, size: 15)
                        Text("Reply")
                    }
                    .meadowButton(small: true)
                }
            }
            Button { actions.open(copy) } label: {
                MeadowIconView(.eye, size: 15)
                Text("Read")
            }
            .meadowButton(.quiet, small: true)
            Spacer(minLength: 4)
            Button { actions.askUntil([copy]) } label: { MeadowIconView(.clock, size: 15) }
                .meadowButton(.quiet, small: true, icon: true)
                .meadowAnchor(MeadowAnchors.cardSnooze(copy))
                .help("Snooze")
                .accessibilityLabel("Snooze “\(subject)”")
            Button { Task { await model.file(item, .archive) } } label: { MeadowIconView(.archive, size: 15) }
                .meadowButton(.quiet, small: true, icon: true)
                .help("File to Archive")
                .accessibilityLabel("Archive “\(subject)”")
            Button { Task { await model.file(item, .trash) } } label: { MeadowIconView(.trash, size: 15) }
                .meadowButton(.quiet, del: true, small: true, icon: true)
                .help("Delete")
                .accessibilityLabel("Delete “\(subject)”")
        }
    }

    /// An address as the page shortens one for a chip: the part before the @.
    static func short(_ address: String) -> String {
        address.split(separator: "@").first.map(String.init) ?? address
    }
}

/// A message worth a read (`.read-i`): a leaf, its subject, who sent it and when, and the gist in moss.
struct ReadRow: View {
    let item: API.DigestItem
    /// Your addresses: your own mail is named by whom it went to (`RowWords.who`).
    var mine: Set<String> = []
    let open: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool
    @State private var hovering = false

    var body: some View {
        Button(action: open) {
            HStack(alignment: .top, spacing: 12) {
                MeadowIconView(.leaf)
                    .foregroundStyle(meadow.ink2)
                    .frame(width: 34, height: 34)
                    .background(RoundedRectangle(cornerRadius: 10).fill(meadow.paper3))
                VStack(alignment: .leading, spacing: 0) {
                    Text(item.subject.isEmpty ? String(localized: "(no subject)") : item.subject)
                        .font(.meadow(.ui, 14, .bold))
                        .foregroundStyle(meadow.ink)
                        .lineLimit(1)
                    Text(RowWords.named(item, mine: mine))
                        .font(.meadow(.ui, 12.5))
                        .foregroundStyle(meadow.ink3)
                        .lineLimit(1)
                    if let gist = item.summary, !gist.isEmpty {
                        Text(gist)
                            .font(.meadow(.ui, 13.5, .medium))
                            .foregroundStyle(meadow.moss)
                            .lineLimit(1)
                            .padding(.top, 4)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Text(item.when)
                    .font(.meadow(.ui, 12.5))
                    .foregroundStyle(meadow.ink3)
                    .lineLimit(1)
            }
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 12).fill(hovering ? meadow.paper2 : .clear))
            .contentShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 12)
        .onHover { hovering = $0 }
    }
}

/// A row of what can wait, or what Tidy filed (`.tidy`): on a dashed card, an icon, what it is in a sentence, and its
/// buttons; below it, while reviewed, the mail itself.
struct TidyRow<Buttons: View, More: View>: View {
    let icon: MeadowIcon
    let title: String
    let words: String
    @ViewBuilder let buttons: Buttons
    @ViewBuilder let more: More
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 14) {
                    sentence.frame(minWidth: 220, maxWidth: .infinity, alignment: .leading)
                    HStack(spacing: 8) { buttons }.fixedSize()
                }
                VStack(alignment: .leading, spacing: 10) {
                    sentence
                    HStack(spacing: 8) { buttons }
                }
            }
            more
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16).fill(meadow.paper2))
        .overlay {
            RoundedRectangle(cornerRadius: 16)
                .strokeBorder(meadow.line2, style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
        }
    }

    private var sentence: some View {
        HStack(spacing: 14) {
            MeadowIconView(icon, size: 22).foregroundStyle(meadow.ink2)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.meadow(.ui, 14.5, .bold)).foregroundStyle(meadow.ink)
                Text(words).font(.meadow(.ui, 13.5)).foregroundStyle(meadow.ink2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// A message in a tidy row's review: who, then what.
struct TidyLine: View {
    let who: String
    let subject: String
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 6) {
            Text(who).font(.meadow(.ui, 13, .bold)).foregroundStyle(meadow.ink)
            Text(subject).font(.meadow(.ui, 13)).foregroundStyle(meadow.ink2)
        }
        .lineLimit(1)
        .padding(.leading, 36)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
#endif
