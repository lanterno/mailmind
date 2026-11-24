#if os(macOS)
import SwiftUI

/// What the brief's buttons do in the window: open a message, go to a stack, open a panel, answer a message.
struct BriefActions {
    var open: (MailCopy) -> Void = { _ in }
    /// The Waiting or Reply Later stack, as the brief's tallies open it.
    var goToStack: (AttentionStack) -> Void = { _ in }
    var commitments: () -> Void = {}
    var week: () -> Void = {}
    /// A reply to a message, where the window has a composer: the reply written ahead when there is one.
    var reply: ((MailCopy) -> Void)?
    var askUntil: ([MailCopy]) -> Void = { _ in }
    /// Whether a reply is written ahead for a message (by its key), so its card offers to use it.
    var draftReady: (Int) -> Bool = { _ in false }
    /// The paper trail, where the window has one.
    var paperTrail: (() -> Void)?
}

/// Today's brief, in the reading pane beside the Inbox, as Meadow draws it (`briefHTML()`): the scene with how the
/// mail stands and a greeting, the brief the AI wrote on a card over the hills with the mail it names as links and
/// the tallies under it, then what needs you, what is worth a read and what can wait, and beside them (below them in a
/// narrow pane) today and next, who you're waiting on, the paper trail and your commitments. The cards, tallies, side
/// and the line over the greeting come from the digest and the mailbox's counts, so they stand with AI off.
struct BriefScreen: View {
    let model: BriefModel
    let attention: AttentionModel
    let actions: BriefActions
    var tasks: TasksModel?
    /// The time a test or a picture shows the brief at. Without one, the greeting, the date and the side follow the
    /// clock: a brief open since the morning says good evening in the evening.
    var now: Date?
    /// The person's first name for the greeting: the window passes the one this Mac knows, a test a fictional one.
    var name: String?
    /// Every account's Inbox unread, which the line over the greeting says; none until the window's counts are read.
    var unread: Int?
    @State private var showAll = false
    @State private var showAllReads = false
    @State private var topic: String?
    @State private var reviewingTidy = false
    @State private var askingTidy = false
    @State private var tidyRunOpen = false
    @State private var width: CGFloat = 1000
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowLight) private var light

    /// The mockup's container queries on the pane: a column beside from 940, the narrow brief to 700.
    private var wide: Bool { width >= 940 }
    private var narrow: Bool { width <= 700 }

    var body: some View {
        ScrollViewReader { scroll in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    hero
                    VStack(alignment: .leading, spacing: 0) {
                        briefCard(scroll)
                            .frame(maxWidth: 800, alignment: .leading)
                            .padding(.top, -84)
                        grid
                            .padding(.top, 28)
                    }
                    .padding(.horizontal, narrow ? 18 : 40)
                    .padding(.bottom, narrow ? 70 : 80)
                    .frame(maxWidth: 1180, alignment: .leading)
                    .frame(maxWidth: .infinity)
                }
            }
            .scrollContentBackground(.hidden)
        }
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
        .confirmationDialog(tidyQuestion, isPresented: $askingTidy) {
            Button("File to Archive") { Task { await model.fileTidy() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("They stay searchable and you can undo it.")
        }
    }

    private var tidyQuestion: String {
        "File " + countNoun(model.tidy.count, "low-priority message", "low-priority messages") + " to Archive?"
    }

    // MARK: The top

    /// The scene, 220 high, with how the mail stands and the greeting on its sky (`.bhero`). The line over the
    /// greeting is the mail's counts, with the date in its place only until they are read: the menu bar shows the
    /// date, and the greeting already says the time of day.
    private var hero: some View {
        ZStack(alignment: .topLeading) {
            MeadowScene(seed: 21)
            BriefClock(fixed: now) { now in
                VStack(alignment: .leading, spacing: 0) {
                    Text(model.line(unread: unread, now: now).uppercased())
                        .font(.meadow(.ui, 12, .heavy))
                        .tracking(12 * 0.14)
                        .foregroundStyle(meadow.ink2)
                    Text(BriefWords.greeting(hour: Calendar.current.component(.hour, from: now), light: light,
                                             name: name))
                        .font(.meadow(.display, narrow ? 30 : 40, .bold))
                        .foregroundStyle(meadow.ink)
                        .lineLimit(2)
                        .padding(.top, 8)
                        .accessibilityAddTraits(.isHeader)
                }
            }
            .padding(.leading, narrow ? 20 : 40)
            .padding(.trailing, 40)
            .padding(.top, narrow ? 22 : 32)
        }
        .frame(height: 220)
        .frame(maxWidth: .infinity)
        .clipped()
    }

    /// The brief on a raised card over the hills (`.brief-card`). With AI turned off it holds only the tallies, under
    /// a plain heading: no brief is written, and nothing asks for one.
    private func briefCard(_ scroll: ScrollViewProxy) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                if model.aiTurnedOff {
                    MeadowIconView(.sun, size: 15).foregroundStyle(meadow.ink3)
                    MeadowSectionLabel(String(localized: "Your day"))
                    Spacer(minLength: 0)
                } else {
                    MeadowAIMark("Your brief")
                    if let written = BriefWords.written(model.brief, reading: model.summarizing) {
                        Text(written)
                            .font(.meadow(.ui, 12.5))
                            .foregroundStyle(meadow.ink3)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 0)
                    Button { Task { await model.write(again: true) } } label: {
                        MeadowIconView(.refresh, size: 15)
                        Text("Refresh")
                    }
                    .meadowButton(.quiet, small: true)
                    .help("Write the brief again from your mail as it is now (runs the AI)")
                    .disabled(model.writing)
                }
            }
            .padding(.bottom, model.aiTurnedOff ? 0 : 10)
            if !model.aiTurnedOff { narrative }
            if !model.aiTurnedOff, !model.topics.isEmpty {
                BriefFlow(spacing: 6) {
                    ForEach(model.topics, id: \.label) { group in
                        BriefTopic(label: group.label, count: group.items.count, on: topic == group.label) {
                            topic = topic == group.label ? nil : group.label
                            scroll.scrollTo(Self.needsID, anchor: .top)
                        }
                    }
                }
                .padding(.top, 12)
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Topics in today’s mail")
            }
            BriefFlow(spacing: 8) {
                ForEach(tallies, id: \.goes) { tally in
                    BriefTallyButton(tally: tally) { go(tally.goes, scroll) }
                }
            }
            .padding(.top, 14)
        }
        .padding(.horizontal, 24)
        .padding(.top, 20)
        .padding(.bottom, 18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .meadowPanel(radius: 18, raised: true)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Today’s brief")
    }

    private var tallies: [BriefWords.Tally] {
        let commitments = BriefWords.commitments(model.items).count + (tasks?.open ?? 0)
        return BriefWords.tallies(needs: model.needs.count, waiting: model.waitingCount ?? 0,
                                  replyLater: model.replyLaterCount ?? 0, worthARead: model.worthARead.count,
                                  canWait: model.tidy.count, commitments: commitments, week: !model.aiTurnedOff)
    }

    private func go(_ goes: BriefWords.Tally.Goes, _ scroll: ScrollViewProxy) {
        switch goes {
        case .needs:
            topic = nil
            showAll = true
            scroll.scrollTo(Self.needsID, anchor: .top)
        case .waiting: actions.goToStack(.waiting)
        case .replyLater: actions.goToStack(.replyLater)
        case .worthARead:
            showAllReads = true
            scroll.scrollTo(Self.readsID, anchor: .top)
        case .canWait:
            reviewingTidy = true
            scroll.scrollTo(Self.tidyID, anchor: .top)
        case .commitments: actions.commitments()
        case .week: actions.week()
        }
    }

    @ViewBuilder private var narrative: some View {
        if model.aiOn == false && (model.brief?.narrative ?? "").isEmpty {
            AIOffNote(ai: model.digest?.ai, preferences: model.preferences)
        } else if model.writing {
            DeskThinking(saying: "Reading your inbox…")
        } else if let narrative = model.brief?.narrative, !narrative.isEmpty {
            BriefText(text: narrative, items: model.items, open: actions.open)
        } else if model.trouble == .limit {
            quiet("Today’s AI limit is reached, so the brief isn’t written yet.", button: "Write it")
        } else if case .failed(let why)? = model.trouble {
            quiet("The brief isn’t available right now (\(model.failing ?? why)).", button: "Try again")
        } else if model.missing, model.aiOn == true {
            quiet("Today’s brief isn’t written yet.", button: "Write it")
        } else if model.summarizing {
            // Nothing summarized to brief from yet, and the AI is reading what waits: it is read again once it has.
            DeskThinking(saying: "Reading your new mail…")
        } else {
            Text("Nothing to brief yet.")
                .font(.meadow(.ui, 15))
                .foregroundStyle(meadow.ink3)
        }
    }

    private func quiet(_ words: String, button: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(words)
            Button(button) { Task { await model.write(again: false) } }
                .deskLink()
        }
        .font(.meadow(.ui, 15))
        .foregroundStyle(meadow.ink3)
    }

    // MARK: Beside each other

    static let needsID = "brief-needs"
    static let readsID = "brief-reads"
    static let tidyID = "brief-tidy"

    @ViewBuilder private var grid: some View {
        if wide {
            HStack(alignment: .top, spacing: 28) {
                main.frame(maxWidth: .infinity, alignment: .leading)
                side.frame(width: 320)
            }
        } else {
            VStack(alignment: .leading, spacing: 28) {
                main
                side
            }
        }
    }

    private var main: some View {
        VStack(alignment: .leading, spacing: 0) {
            needsSection.id(Self.needsID)
            readsSection.id(Self.readsID)
            tidySection.id(Self.tidyID)
        }
    }

    /// A section's heading (`.sec-h`), with a note at its end; in a topic, the way back to what needs you.
    private func sectionHead(_ title: String, note: String? = nil, inTopic: Bool = false) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(title)
                .font(.meadow(.display, 19, .bold))
                .foregroundStyle(meadow.ink)
                .accessibilityAddTraits(.isHeader)
            if inTopic {
                Button("Show what needs you") { topic = nil }.deskLink()
            }
            Spacer(minLength: 0)
            if let note {
                Text(note).font(.meadow(.ui, 12.5)).foregroundStyle(meadow.ink3)
            }
        }
        .padding(.bottom, 12)
    }

    // MARK: What needs you

    @ViewBuilder private var needsSection: some View {
        let topical = topic.flatMap { label in model.topics.first { $0.label == label }?.items }
        let needs = topical ?? model.needs
        let shown = showAll || topical != nil ? needs : Array(needs.prefix(BriefWords.briefCap))
        VStack(alignment: .leading, spacing: 0) {
            sectionHead(topic ?? String(localized: "Needs you"),
                        note: needs.isEmpty ? nil : String(localized: "click a title to read it here"),
                        inTopic: topic != nil)
            if needs.isEmpty {
                Text(model.calm)
                    .font(.meadow(.ui, 14.5))
                    .foregroundStyle(meadow.ink3)
            }
            VStack(spacing: 10) {
                ForEach(shown, id: \.self) { item in
                    NeedCard(item: item, model: model, attention: attention, actions: actions)
                }
            }
            if needs.count > shown.count {
                let all = BriefWords.Tally(words: String(localized: "Show all \(needs.count)"), goes: .needs)
                BriefTallyButton(tally: all) { showAll = true }
                .padding(.top, 12)
            }
        }
    }

    // MARK: Worth a read

    @ViewBuilder private var readsSection: some View {
        let reads = model.worthARead
        if !reads.isEmpty {
            let shown = showAllReads ? reads : Array(reads.prefix(3))
            VStack(alignment: .leading, spacing: 0) {
                sectionHead(String(localized: "Worth a read"))
                VStack(spacing: 2) {
                    ForEach(shown, id: \.self) { item in
                        ReadRow(item: item, mine: model.mine) { actions.open(BriefModel.copy(of: item)) }
                    }
                }
                if reads.count > shown.count {
                    Button("Show all \(reads.count)") { showAllReads = true }
                        .deskLink()
                        .padding(.top, 6)
                }
            }
            .padding(.top, 28)
        }
    }

    // MARK: What can wait

    @ViewBuilder private var tidySection: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let run = model.tidyShown {
                let filed = countNoun(run.filed, "low-priority message", "low-priority messages")
                TidyRow(icon: .wind, title: String(localized: "We filed \(filed) for you"),
                        words: String(localized: """
                            Each one is in its mailbox’s Archive, and Undo brings them all back.
                            """)) {
                    Button(tidyRunOpen ? "Hide" : "Review") { tidyRunOpen.toggle() }.meadowButton(.quiet)
                    Button("Undo") { Task { await model.undoTidy() } }.meadowButton()
                    Button("Dismiss") { model.dismissTidy() }.meadowButton(.quiet)
                } more: {
                    if tidyRunOpen {
                        ForEach(Array(run.items.enumerated()), id: \.offset) { _, filed in
                            TidyLine(who: filed.who ?? String(localized: "Unknown"),
                                     subject: filed.subject ?? String(localized: "(no subject)"))
                        }
                    }
                }
            }
            let tidy = model.tidy
            if !tidy.isEmpty {
                TidyRow(icon: .archive, title: tidy.count == 1 ? String(localized: "1 message can wait")
                            : String(localized: "\(tidy.count) messages can wait"),
                        words: String(localized: """
                            The AI rates them as safe to file: no next step, nothing asked of you.
                            """)) {
                    Button(reviewingTidy ? "Hide" : "Review") { reviewingTidy.toggle() }.meadowButton(.quiet)
                    Button("File \(tidy.count) to Archive") { askingTidy = true }.meadowButton()
                } more: {
                    if reviewingTidy {
                        ForEach(tidy, id: \.self) { item in
                            let subject = item.subject.isEmpty ? String(localized: "(no subject)") : item.subject
                            Button { actions.open(BriefModel.copy(of: item)) } label: {
                                TidyLine(who: RowWords.named(item, mine: model.mine), subject: subject)
                            }
                            .deskTitle()
                        }
                    }
                }
            }
        }
        .padding(.top, 24)
    }

    // MARK: Your day

    private var side: some View {
        BriefClock(fixed: now) { now in
            BriefSide(model: model, tasks: tasks, attention: attention, actions: actions, now: now)
        }
    }
}

/// What the brief says by the time of day, at the time `fixed` when a test or a picture gives one, else at the
/// clock's, read again as each minute turns (`schedule`). Nothing runs between the turns, and only what reads the
/// time is drawn again.
struct BriefClock<Schedule: TimelineSchedule, Content: View>: View {
    var fixed: Date?
    var schedule: Schedule
    @ViewBuilder var content: (Date) -> Content

    var body: some View {
        if let fixed {
            content(fixed)
        } else {
            TimelineView(schedule) { context in content(context.date) }
        }
    }
}

extension BriefClock where Schedule == EveryMinuteTimelineSchedule {
    init(fixed: Date?, @ViewBuilder content: @escaping (Date) -> Content) {
        self.init(fixed: fixed, schedule: .everyMinute, content: content)
    }
}

/// AI off for want of something, in the page's words for the provider chosen: why, where in Settings it turns on, and
/// what AI mode would send, to whom and when (`AIWords.sends`), as Settings says it.
struct AIOffNote: View {
    /// How AI stands: off for want of a key, a model or a URL says why and where it turns on; turned off, nothing is
    /// said at all.
    let ai: API.AiStatus?
    /// Settings' choices: the provider's URL and model, and whether replies are drafted ahead.
    var preferences: API.Preferences? = nil
    @Environment(\.meadow) private var meadow

    var body: some View {
        if let ai, let note = AIWords.offNote(ai) {
            VStack(alignment: .leading, spacing: 7) {
                Text("\(Text(note.why).bold()) Summaries, this brief and reply drafts come from \(ai.provider.writer). Your mail works the same without them.")
                    .font(.meadow(.ui, 14.5))
                    .foregroundStyle(meadow.ink2)
                    .lineSpacing(MeadowFace.ui.lineSpacing(size: 14.5, lineHeight: 1.55))
                    .fixedSize(horizontal: false, vertical: true)
                Text(note.fix)
                    .font(.meadow(.ui, 13))
                    .foregroundStyle(meadow.ink3)
                HStack(alignment: .firstTextBaseline, spacing: 7) {
                    MeadowIconView(.lock, size: 14).alignmentGuide(.firstTextBaseline) { $0[.bottom] - 2 }
                    Text(AIWords.sends(preferences, ai))
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(.meadow(.ui, 13))
                .foregroundStyle(meadow.ink3)
            }
        }
    }
}

/// The brief's text in the AI's hand (`.brief-text`): Klee One in moss, each message it names a link that opens it.
struct BriefText: View {
    let text: String
    let items: [API.DigestItem]
    let open: (MailCopy) -> Void
    @Environment(\.meadow) private var meadow

    var body: some View {
        let runs = BriefWords.runs(text, items)
        Text(attributed(runs))
            .font(.meadow(.hand, 18, .semibold))
            .foregroundStyle(meadow.moss)
            .tint(meadow.ink)
            .lineSpacing(MeadowFace.hand.lineSpacing(size: 18, lineHeight: 1.7, weight: .semibold))
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
            .environment(\.openURL, OpenURLAction { url in
                guard let at = Self.index(url), runs.indices.contains(at), let item = runs[at].names else {
                    return .discarded
                }
                open(BriefModel.copy(of: item))
                return .handled
            })
    }

    /// The runs as one text, each link carrying its run's place.
    private func attributed(_ runs: [BriefWords.Run]) -> AttributedString {
        var all = AttributedString()
        for (at, run) in runs.enumerated() {
            var piece = AttributedString(run.text)
            if run.names != nil {
                piece.link = URL(string: "mailmind-brief:run/\(at)")
                piece[AttributeScopes.SwiftUIAttributes.ForegroundColorAttribute.self] = meadow.ink
                piece[AttributeScopes.SwiftUIAttributes.UnderlineStyleAttribute.self] =
                    Text.LineStyle(pattern: .solid, color: meadow.moss.opacity(0.45))
            }
            all += piece
        }
        return all
    }

    static func index(_ url: URL) -> Int? {
        guard url.scheme == "mailmind-brief" else { return nil }
        return Int(url.lastPathComponent)
    }
}

/// A tally under the brief (`.tally`): a pill with its count and its words, in sunlight for what needs you.
struct BriefTallyButton: View {
    let tally: BriefWords.Tally
    let action: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool
    @State private var hovering = false

    var body: some View {
        let sun = tally.sun
        Button(action: action) {
            HStack(spacing: 8) {
                if sun { Circle().fill(meadow.sun).frame(width: 8, height: 8) }
                if let icon = Self.icon(tally.goes) { MeadowIconView(icon, size: 15) }
                if let count = tally.count {
                    Text(count, format: .number)
                        .font(.meadow(.ui, 15, .heavy))
                        .monospacedDigit()
                        .foregroundStyle(sun ? meadow.sunInk : meadow.ink)
                }
                Text(tally.words)
            }
            .font(.meadow(.ui, 13.5, .bold))
            .foregroundStyle(sun ? meadow.sunInk : hovering ? meadow.ink : meadow.ink2)
            .padding(.horizontal, 15)
            .frame(minHeight: 36)
            .background(Capsule().fill(sun ? meadow.sunWash : meadow.paper))
            .overlay {
                Capsule().strokeBorder(sun ? meadow.sun.opacity(0.45) : hovering ? meadow.ink3 : meadow.line2,
                                       lineWidth: 1)
            }
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 18)
        .onHover { hovering = $0 }
        .help(tally.goes == .needs ? String(localized: "List them") : "")
    }

    static func icon(_ goes: BriefWords.Tally.Goes) -> MeadowIcon? {
        switch goes {
        case .commitments: .todo
        case .week: .cal
        default: nil
        }
    }
}

/// One of the brief's topics (`.bf-topic`): a quieter pill, which narrows what needs you to the topic's mail.
struct BriefTopic: View {
    let label: String
    let count: Int
    let on: Bool
    let action: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(label)
                Text(count, format: .number).monospacedDigit().foregroundStyle(on ? meadow.primaryInk : meadow.ink3)
            }
            .font(.meadow(.ui, 12.5, .semibold))
            .foregroundStyle(on ? meadow.primaryInk : meadow.ink)
            .padding(.horizontal, 11)
            .frame(height: 26)
            .background(Capsule().fill(on ? meadow.primaryWash : hovering ? meadow.paper3 : meadow.paper2))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 13)
        .onHover { hovering = $0 }
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// Pills in rows that wrap at the width they have.
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
