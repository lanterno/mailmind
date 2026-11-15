#if os(macOS)
import SwiftUI

/// What the brief's buttons do in the window: open a message, go to a stack, open a panel, answer a message.
struct BriefActions {
    var open: (MailCopy) -> Void = { _ in }
    /// The Waiting or Reply Later stack, as the brief's tallies open it.
    var goToStack: (AttentionStack) -> Void = { _ in }
    var commitments: () -> Void = {}
    var week: () -> Void = {}
    /// A reply to a message, where the window has a composer.
    var reply: ((MailCopy) -> Void)?
    var askUntil: ([MailCopy]) -> Void = { _ in }
}

/// Today's brief, in the list's place: a greeting, what Claude made of today's mail, what needs you, what can be
/// filed, and beside it today and next and who you're waiting on. The cards, tallies and side come from the digest,
/// so they stand with AI off.
struct BriefScreen: View {
    let model: BriefModel
    let attention: AttentionModel
    let actions: BriefActions
    var now = Date()
    @State private var showAll = false
    @State private var topic: String?
    @State private var reviewingTidy = false
    @State private var askingTidy = false
    @State private var tidyRunOpen = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                hero
                briefCard
                needsSection
                tidySection
                side
            }
            .padding(16)
            .frame(maxWidth: 720, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .confirmationDialog(tidyQuestion, isPresented: $askingTidy) {
            Button("File to Archive") { Task { await model.fileTidy() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("They stay searchable and you can undo it.")
        }
        .toolbar {
            ToolbarItem {
                Button("Refresh Brief", systemImage: "arrow.clockwise") { Task { await model.write(again: true) } }
                    .help("Write the brief again from your mail as it is now (runs the AI)")
                    .disabled(model.writing)
            }
        }
    }

    private var tidyQuestion: String {
        "File " + countNoun(model.tidy.count, "low-priority message", "low-priority messages") + " to Archive?"
    }

    // MARK: The top

    private var hero: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(now.formatted(.dateTime.weekday(.wide).day().month(.wide)))
                .font(.callout.weight(.medium))
                .foregroundStyle(.secondary)
            Text(verbatim: BriefWords.greeting(hour: Calendar.current.component(.hour, from: now)) + ".")
                .font(.largeTitle.weight(.semibold))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 18)
        .padding(.vertical, 20)
        .background(
            LinearGradient(colors: [Color.orange.opacity(0.22), Color.yellow.opacity(0.10), Color.blue.opacity(0.12)],
                           startPoint: .topLeading, endPoint: .bottomTrailing),
            in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private var briefCard: some View {
        DeskCard {
            DeskCardHeading(title: "Your Brief", symbol: "sparkles")
            narrative
            if !model.topics.isEmpty {
                BriefFlow {
                    ForEach(model.topics, id: \.label) { group in
                        BriefChip(text: group.label, count: group.items.count, on: topic == group.label) {
                            topic = topic == group.label ? nil : group.label
                        }
                    }
                }
                .accessibilityLabel("Topics in today’s mail")
            }
            BriefFlow {
                let needs = model.needs.count
                if needs > 0 {
                    BriefChip(text: needs == 1 ? "needs you" : "need you", count: needs, sun: true) { topic = nil; showAll = true }
                        .help("List them")
                }
                if model.waitingCount > 0 {
                    BriefChip(text: "waiting on others", count: model.waitingCount) { actions.goToStack(.waiting) }
                }
                if model.replyLaterCount > 0 {
                    BriefChip(text: "to reply later", count: model.replyLaterCount) { actions.goToStack(.replyLater) }
                }
                let commits = BriefWords.commitments(model.items).count
                if commits > 0 {
                    BriefChip(text: commits == 1 ? "commitment" : "commitments", count: commits, symbol: "checklist",
                         action: actions.commitments)
                }
                BriefChip(text: "Your week", symbol: "calendar", action: actions.week)
            }
        }
    }

    @ViewBuilder private var narrative: some View {
        if model.aiOn == false && (model.brief?.narrative ?? "").isEmpty {
            AIOffNote()
        } else if model.writing {
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("Reading your inbox…")
            }
            .foregroundStyle(.secondary)
        } else if let narrative = model.brief?.narrative, !narrative.isEmpty {
            Text(narrative).textSelection(.enabled)
        } else if model.trouble == .limit {
            HStack {
                Text("Today’s AI limit is reached, so the brief isn’t written yet.")
                Button("Write It") { Task { await model.write(again: false) } }.buttonStyle(.link)
            }
            .foregroundStyle(.secondary)
        } else if case .failed(let why)? = model.trouble {
            HStack {
                Text("The brief isn’t available right now (\(model.failing ?? why)).")
                Button("Try Again") { Task { await model.write(again: false) } }.buttonStyle(.link)
            }
            .foregroundStyle(.secondary)
        } else {
            Text("Nothing to brief yet.").foregroundStyle(.secondary)
        }
    }

    // MARK: What needs you

    @ViewBuilder private var needsSection: some View {
        let topical = topic.flatMap { label in model.topics.first { $0.label == label }?.items }
        let needs = topical ?? model.needs
        let shown = showAll || topical != nil ? needs : Array(needs.prefix(BriefWords.briefCap))
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(topic ?? String(localized: "Needs You")).font(.title3.weight(.semibold))
                if topic != nil {
                    Button("Show What Needs You") { topic = nil }.buttonStyle(.link)
                }
            }
            if needs.isEmpty {
                Text("Nothing needs you right now.").foregroundStyle(.secondary)
            }
            ForEach(shown, id: \.self) { item in
                NeedCard(item: item, multi: model.multi, model: model, attention: attention, actions: actions)
            }
            if needs.count > shown.count {
                Button("Show All \(needs.count)") { showAll = true }
            }
        }
    }

    // MARK: Filing

    @ViewBuilder private var tidySection: some View {
        if let run = model.tidyShown {
            DeskCard(tint: .green) {
                Label {
                    Text("We filed \(countNoun(run.filed, "low-priority message", "low-priority messages")) for you.")
                } icon: {
                    Image(systemName: "sparkles")
                }
                HStack {
                    Button(tidyRunOpen ? "Hide" : "Review") { tidyRunOpen.toggle() }
                    Button("Undo") { Task { await model.undoTidy() } }
                    Button("Dismiss") { model.dismissTidy() }
                }
                .controlSize(.small)
                if tidyRunOpen {
                    ForEach(Array(run.items.enumerated()), id: \.offset) { _, filed in
                        Text("**\(filed.who ?? String(localized: "Unknown"))** \(filed.subject ?? String(localized: "(no subject)"))")
                            .font(.callout)
                    }
                }
            }
        }
        let tidy = model.tidy
        if !tidy.isEmpty {
            DeskCard(tint: .green) {
                Label {
                    Text("\(countNoun(tidy.count, "low-priority message", "low-priority messages")) the AI rates as safe to file: no next step, nothing asked of you.")
                } icon: {
                    Image(systemName: "sparkles")
                }
                HStack {
                    Button(reviewingTidy ? "Hide" : "Review") { reviewingTidy.toggle() }
                    Button("File to Archive") { askingTidy = true }
                }
                .controlSize(.small)
                if reviewingTidy {
                    ForEach(tidy, id: \.self) { item in
                        Button {
                            actions.open(BriefModel.copy(of: item))
                        } label: {
                            Text("**\(item.fromName ?? item.fromAddr ?? "—")** \(item.subject.isEmpty ? String(localized: "(no subject)") : item.subject)")
                                .font(.callout)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    // MARK: Your day

    @ViewBuilder private var side: some View {
        let upcoming = Array(model.upcoming.prefix(4))
        if !upcoming.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                Label("Today & Next", systemImage: "calendar").font(.headline)
                UpcomingList(events: upcoming, now: now, open: actions.open)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quinary, in: RoundedRectangle(cornerRadius: 10))
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Today and next")
        }
        let waiting = model.waiting
        if !waiting.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Label("Waiting on Others", systemImage: "hourglass").font(.headline)
                ForEach(waiting.prefix(3), id: \.self) { row in
                    let who = BriefWords.nameIn(row.to)
                    Button {
                        actions.open(.named(key: row.key, folder: row.folder, uid: row.uid, account: row.account))
                    } label: {
                        HStack(spacing: 8) {
                            SenderInitials(name: who, address: row.to, size: 24)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(who.isEmpty ? "—" : who).fontWeight(.medium)
                                Text(verbatim: [row.subject.isEmpty ? String(localized: "(no subject)") : row.subject,
                                                BriefWords.waitAge(sent: row.ts, now: now),
                                                model.multi ? (model.digest?.aliases[row.account] ?? nil) ?? row.account : nil]
                                    .compactMap { $0 }.joined(separator: " · "))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            Spacer(minLength: 0)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
                if waiting.count > 3 {
                    Button("See All \(waiting.count)") { actions.goToStack(.waiting) }.buttonStyle(.link)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quinary, in: RoundedRectangle(cornerRadius: 10))
        }
    }
}

/// AI off, in the page's words, with where a key goes in this app.
struct AIOffNote: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("**AI is off.** Summaries, this brief and reply drafts come from Claude, and MailMind has no Anthropic API key. Your mail works the same without it.")
            Text("To turn it on, add your key in MailMind’s Settings.")
                .foregroundStyle(.secondary)
            Label("With AI on, the text of a message goes to Anthropic only when it is summarized, briefed or drafted; nothing else leaves this Mac.",
                  systemImage: "lock")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

/// One message that needs you: who, when, what, what Claude made of it, the next step, and what to do about it.
struct NeedCard: View {
    let item: API.DigestItem
    let multi: Bool
    let model: BriefModel
    let attention: AttentionModel
    let actions: BriefActions

    var body: some View {
        let copy = BriefModel.copy(of: item)
        let subject = item.subject.isEmpty ? String(localized: "(no subject)") : item.subject
        HStack(alignment: .top, spacing: 10) {
            SenderInitials(name: item.fromName, address: item.fromAddr, size: 32)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(item.fromName ?? item.fromAddr ?? "—").fontWeight(.semibold).lineLimit(1)
                    if multi {
                        CardTag(LocalizedStringKey(item.alias.flatMap { $0.isEmpty ? nil : $0 } ?? Self.short(item.account)))
                    }
                    Spacer(minLength: 4)
                    Text(item.when).font(.caption).foregroundStyle(.secondary)
                }
                Button(subject) { actions.open(copy) }
                    .buttonStyle(.link)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                if let summary = item.summary, !summary.isEmpty {
                    Text(summary).font(.callout).foregroundStyle(.secondary)
                }
                if !item.action.trimmingCharacters(in: .whitespaces).isEmpty {
                    Text(item.action)
                        .font(.caption.weight(.medium))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 2)
                        .background(Color.orange.opacity(0.18), in: Capsule())
                }
                HStack(spacing: 6) {
                    if let reply = actions.reply {
                        Button("Reply", systemImage: "arrowshape.turn.up.left") { reply(copy) }
                    }
                    Button("Read", systemImage: "eye") { actions.open(copy) }
                    Spacer()
                    SnoozeMenu(copies: [copy], stack: nil, model: attention, askUntil: actions.askUntil)
                        .labelStyle(.iconOnly)
                        .menuIndicator(.hidden)
                        .fixedSize()
                        .help("Snooze")
                        .accessibilityLabel("Snooze “\(subject)”")
                    Button("Archive", systemImage: "archivebox") { Task { await model.file(item, .archive) } }
                        .labelStyle(.iconOnly)
                        .help("Archive")
                        .accessibilityLabel("Archive “\(subject)”")
                    Button("Delete", systemImage: "trash") { Task { await model.file(item, .trash) } }
                        .labelStyle(.iconOnly)
                        .help("Delete")
                        .accessibilityLabel("Delete “\(subject)”")
                }
                .controlSize(.small)
                .buttonStyle(.borderless)
                .padding(.top, 2)
            }
        }
        .padding(12)
        .background(.background, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(.separator))
    }

    /// An address as the page shortens one for a chip: the part before the @.
    static func short(_ address: String) -> String {
        address.split(separator: "@").first.map(String.init) ?? address
    }
}

/// A tally or a topic: a count and its words, pressed to go there.
struct BriefChip: View {
    let text: LocalizedStringKey
    var count: Int?
    var symbol: String?
    var on = false
    var sun = false
    let action: () -> Void

    init(text: String, count: Int? = nil, symbol: String? = nil, on: Bool = false, sun: Bool = false,
         action: @escaping () -> Void) {
        self.text = LocalizedStringKey(text)
        self.count = count
        self.symbol = symbol
        self.on = on
        self.sun = sun
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                if let symbol { Image(systemName: symbol) }
                if let count { Text(count, format: .number).fontWeight(.bold).monospacedDigit() }
                Text(text)
            }
            .font(.callout)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(sun ? AnyShapeStyle(Color.orange.opacity(0.25)) : on ? AnyShapeStyle(.selection) : AnyShapeStyle(.quaternary),
                        in: Capsule())
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

/// Chips in rows that wrap at the width they have.
struct BriefFlow: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews)
        return CGSize(width: proposal.width ?? rows.map(\.width).max() ?? 0,
                      height: rows.map(\.height).reduce(0, +) + spacing * CGFloat(max(rows.count - 1, 0)))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(width: bounds.width, subviews) {
            var x = bounds.minX
            for index in row.items {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row {
        var items: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(width: CGFloat, _ subviews: Subviews) -> [Row] {
        var rows = [Row()]
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            if !rows[rows.count - 1].items.isEmpty, rows[rows.count - 1].width + spacing + size.width > width {
                rows.append(Row())
            }
            var row = rows[rows.count - 1]
            row.width += (row.items.isEmpty ? 0 : spacing) + size.width
            row.height = max(row.height, size.height)
            row.items.append(index)
            rows[rows.count - 1] = row
        }
        return rows.filter { !$0.items.isEmpty }
    }
}
#endif
