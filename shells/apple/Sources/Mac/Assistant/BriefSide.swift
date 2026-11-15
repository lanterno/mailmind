#if os(macOS)
import SwiftUI

/// Beside what needs you (`.bside`, "Your day"): today and next, who you're waiting on, the paper trail's month, and
/// your commitments, each a side card, each only when it has something to show.
struct BriefSide: View {
    let model: BriefModel
    let tasks: TasksModel?
    let attention: AttentionModel
    let actions: BriefActions
    let now: Date
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            today
            waiting
            paperTrail
            commitments
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Your day")
    }

    // MARK: Today and next

    @ViewBuilder private var today: some View {
        let upcoming = Array(model.upcoming.prefix(4))
        if !upcoming.isEmpty {
            DeskSideCard(title: String(localized: "Today & next"), icon: .cal) {
                UpcomingList(events: upcoming, now: now, open: actions.open)
            }
        }
    }

    // MARK: Waiting on others

    @ViewBuilder private var waiting: some View {
        let waiting = model.waiting
        if !waiting.isEmpty {
            DeskSideCard(title: String(localized: "Waiting on others"), icon: .hourglass) {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(waiting.prefix(3), id: \.self) { row in
                        WaitingLine(row: row, model: model, now: now) {
                            actions.open(.named(key: row.key, folder: row.folder, uid: row.uid, account: row.account))
                        }
                    }
                }
                if waiting.count > 3 {
                    Button("See all \(waiting.count)") { actions.goToStack(.waiting) }.deskLink()
                }
            }
        }
    }

    // MARK: The paper trail

    @ViewBuilder private var paperTrail: some View {
        let months = Ledger.months(model.receipts)
        if let index = months.firstIndex(where: { !$0.yearMonth.isEmpty && !$0.totals.isEmpty }),
           let summary = Ledger.summary(months, at: index) {
            let month = Ledger.monthOnly(summary.yearMonth)
            DeskSideCard(title: String(localized: "Paper trail · \(month)"), icon: .receipt) {
                if let paperTrail = actions.paperTrail {
                    Button("Open", action: paperTrail).deskLink()
                }
            } content: {
                Text(Ledger.money(summary.currency, summary.total))
                    .font(.meadow(.display, 28, .bold))
                    .monospacedDigit()
                    .foregroundStyle(meadow.ink)
                Text(Ledger.words(summary))
                    .font(.meadow(.ui, 12.5))
                    .foregroundStyle(meadow.ink3)
                    .padding(.top, -2)
                LedgerBars(heights: Ledger.bars(months, at: index))
                    .padding(.top, 4)
            }
        }
    }

    // MARK: Commitments

    @ViewBuilder private var commitments: some View {
        let open = (tasks?.tasks ?? []).filter { !$0.done }
        let done = (tasks?.tasks ?? []).filter(\.done)
        let mail = BriefWords.commitments(model.items)
        let lines = Array((open.map(Commitment.task) + mail.map(Commitment.mail) + done.map(Commitment.task)).prefix(4))
        if !lines.isEmpty {
            DeskSideCard(title: String(localized: "Commitments"), icon: .check) {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(lines) { line in
                        CommitmentLine(line: line, model: model, tasks: tasks, open: actions.open)
                    }
                }
                let all = open.count + mail.count
                if all > lines.count(where: { !$0.done }) {
                    Button("See all \(all)", action: actions.commitments).deskLink()
                }
            }
        }
    }
}

/// Someone you're waiting on (`.who-r`): their avatar, their name, and what about and for how long.
private struct WaitingLine: View {
    let row: API.WaitingRow
    let model: BriefModel
    let now: Date
    let open: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool
    @State private var hovering = false

    var body: some View {
        let who = BriefWords.nameIn(row.to)
        Button(action: open) {
            HStack(spacing: 10) {
                MeadowAvatar(name: who.isEmpty ? row.to : who)
                VStack(alignment: .leading, spacing: 1) {
                    Text(who.isEmpty ? "—" : who)
                        .font(.meadow(.ui, 14, .bold))
                        .foregroundStyle(meadow.ink)
                        .underline(hovering)
                        .lineLimit(1)
                    Text(verbatim: [row.subject.isEmpty ? String(localized: "(no subject)") : row.subject,
                                    BriefWords.waitAge(sent: row.ts, now: now),
                                    model.multi ? (model.digest?.aliases[row.account] ?? nil) ?? row.account : nil]
                        .compactMap { $0 }.joined(separator: " · "))
                        .font(.meadow(.ui, 12.5))
                        .foregroundStyle(meadow.ink3)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            .padding(.vertical, 2)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 8)
        .onHover { hovering = $0 }
    }
}

/// A month's bars (`.bars`): the months before it in the line's colour, the month itself in indigo.
struct LedgerBars: View {
    let heights: [Double]
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(alignment: .bottom, spacing: 5) {
            ForEach(Array(heights.enumerated()), id: \.offset) { at, height in
                UnevenRoundedRectangle(topLeadingRadius: 4, bottomLeadingRadius: 2, bottomTrailingRadius: 2,
                                       topTrailingRadius: 4)
                    .fill(at == heights.count - 1 ? meadow.primary : meadow.line2)
                    .frame(maxWidth: .infinity)
                    .frame(height: max(3, 40 * height))
            }
        }
        .frame(height: 40, alignment: .bottom)
        .accessibilityHidden(true)
    }
}

/// A commitment beside the brief: a task you confirmed, or a to-do the AI found in your mail.
enum Commitment: Identifiable {
    case task(API.Task)
    case mail(API.DigestItem)

    var id: String {
        switch self {
        case .task(let task): "task-\(task.id)"
        case .mail(let item): "mail-\(item.key)-\(item.folder)-\(item.uid)"
        }
    }

    var done: Bool {
        if case .task(let task) = self { return task.done }
        return false
    }
}

/// One commitment (`.commit label`): a box to tick and what to do, with whom and by when under it. Ticking a task
/// marks it done; ticking a to-do from the mail files its message, as Commitments does.
private struct CommitmentLine: View {
    let line: Commitment
    let model: BriefModel
    let tasks: TasksModel?
    let open: (MailCopy) -> Void
    @Environment(\.meadow) private var meadow
    @State private var hovering = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            DeskCheckbox(on: line.done, label: label) { tick() }
            Button { if let copy { open(copy) } } label: {
                VStack(alignment: .leading, spacing: 1) {
                    Text(title)
                        .font(.meadow(.ui, 13.5))
                        .foregroundStyle(line.done ? meadow.ink3 : meadow.ink)
                        .strikethrough(line.done, color: meadow.ink3)
                        .multilineTextAlignment(.leading)
                    Text(note)
                        .font(.meadow(.ui, 12))
                        .foregroundStyle(meadow.ink3)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .deskTitle()
            .disabled(copy == nil)
        }
        .padding(.horizontal, 4)
        .padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 8).fill(hovering ? meadow.paper2 : .clear))
        .onHover { hovering = $0 }
    }

    private var title: String {
        switch line {
        case .task(let task): task.title
        case .mail(let item): item.action.isEmpty ? item.subject : item.action
        }
    }

    private var note: String {
        switch line {
        case .task(let task):
            let due = BriefWords.due(task.due, today: BriefWords.localDay()).words
            return [task.fromName ?? task.fromAddr, due.isEmpty ? nil : due].compactMap { $0 }.joined(separator: " · ")
        case .mail(let item):
            return [item.fromName ?? item.fromAddr, item.when].compactMap { $0 }.joined(separator: " · ")
        }
    }

    private var label: String {
        switch line {
        case .task(let task): task.done ? String(localized: "Mark not done") : String(localized: "Mark done")
        case .mail: String(localized: "Done — archive")
        }
    }

    private var copy: MailCopy? {
        switch line {
        case .task(let task): TasksModel.copy(of: task)
        case .mail(let item): BriefModel.copy(of: item)
        }
    }

    private func tick() {
        switch line {
        case .task(let task): Task { await tasks?.mark(task, done: !task.done) }
        case .mail(let item): Task { await model.file(item, .archive) }
        }
    }
}

/// A box to tick (`.commit input`): an outline in the quiet ink, filled with indigo and a tick once ticked.
struct DeskCheckbox: View {
    let on: Bool
    let label: String
    let action: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool

    var body: some View {
        Button(action: action) {
            RoundedRectangle(cornerRadius: 4)
                .fill(on ? meadow.primary : meadow.paperRaised)
                .overlay {
                    if on {
                        MeadowIconView(.check, size: 14, stroke: 2.6).foregroundStyle(meadow.onPrimary)
                    } else {
                        RoundedRectangle(cornerRadius: 4).strokeBorder(meadow.ink3, lineWidth: 1.5)
                    }
                }
                .frame(width: 18, height: 18)
                .padding(.top, 1)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 4)
        .help(label)
        .accessibilityLabel(Text(label))
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}
#endif
