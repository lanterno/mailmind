#if os(macOS)
import Observation
import SwiftUI

/// The tasks someone confirmed from their mail (`/api/tasks`): read, marked done or not, removed.
@MainActor
@Observable
final class TasksModel {
    private(set) var tasks: [API.Task] = []
    @ObservationIgnored let link: CoreLink
    @ObservationIgnored let notices: DeskNotices

    init(link: CoreLink, notices: DeskNotices) {
        self.link = link
        self.notices = notices
    }

    var open: Int { tasks.filter { !$0.done }.count }

    func load() async {
        guard let routes = link.routes, let read = try? await routes.get(API.Get.Tasks.self, [:]) else { return }
        tasks = read.items
    }

    func mark(_ task: API.Task, done: Bool) async {
        await change { try await $0.post(API.Post.TasksDone.self, API.TaskDoneBody(done: done, id: task.id)) }
    }

    func remove(_ task: API.Task) async {
        await change { try await $0.post(API.Post.TasksRemove.self, API.TaskRemoveBody(id: task.id)) }
    }

    private func change(_ call: (any RouteCaller) async throws -> API.TaskGone) async {
        guard let routes = link.routes else { return }
        do {
            _ = try await call(routes)
        } catch {
            notices.say(DeskRefusal.words(error, else: String(localized: "Couldn’t change that task.")))
        }
        await load()
    }

    /// The task's message as it is now; none once no copy is left.
    static func copy(of task: API.Task) -> MailCopy? {
        guard let key = task.key, let folder = task.folder, let uid = task.uid else { return nil }
        return .named(key: key, folder: folder, uid: uid, account: task.account)
    }
}

/// The week behind you (`/api/review`): a read finds the stored one; none yet and you opened it, so it is written.
@MainActor
@Observable
final class ReviewModel {
    enum Stage: Equatable {
        case reading
        case aiOff
        case limit
        case failed(String)
        case written(API.Review)
    }

    private(set) var stage: Stage?
    @ObservationIgnored let link: CoreLink

    init(link: CoreLink) {
        self.link = link
    }

    var review: API.Review? {
        if case .written(let review) = stage { return review }
        return nil
    }

    /// Open it: the stored review, or a new one written.
    func open() async {
        guard let routes = link.routes else { return }
        if review != nil { return }
        stage = .reading
        do {
            switch try await routes.get(API.Get.Review.self, [:]) {
            case .review(let stored): stage = .written(stored)
            case .reviewMissing: stage = .written(try await routes.post(API.Post.Review.self, API.NoBody()))
            }
        } catch {
            stage = Self.stage(error)
        }
    }

    /// Write it again (runs the AI).
    func again() async {
        guard let routes = link.routes else { return }
        stage = .reading
        do {
            stage = .written(try await routes.post(API.Post.ReviewRefresh.self, API.NoBody()))
        } catch {
            stage = Self.stage(error)
        }
    }

    static func stage(_ error: any Error) -> Stage {
        DeskRefusal.isAIOff(error) ? .aiOff : DeskRefusal.isOverLimit(error) ? .limit
            : .failed(DeskRefusal.words(error, else: String(localized: "Couldn’t build your review.")))
    }
}

/// Commitments: the tasks you confirmed, then the AI's to-dos pulled from your mail, as one checkable agenda.
struct CommitmentsSheet: View {
    let tasks: TasksModel
    let brief: BriefModel
    let attention: AttentionModel
    let open: (MailCopy) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let all = BriefWords.commitments(brief.items)
        let shown = Array(all.prefix(BriefWords.commitCap))
        VStack(alignment: .leading, spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if !tasks.tasks.isEmpty {
                        heading("Your Tasks", symbol: "checkmark.circle", count: "\(tasks.open)",
                                note: "the ones you confirmed from your mail")
                        VStack(spacing: 2) {
                            ForEach(tasks.tasks, id: \.id) { task in TaskRow(task: task, tasks: tasks, open: openAndClose) }
                        }
                    }
                    heading("Commitments", symbol: "checklist",
                            count: all.count > BriefWords.commitCap ? "\(all.count) · top \(BriefWords.commitCap)" : "\(all.count)",
                            note: "the AI’s to-dos pulled from your mail")
                    if shown.isEmpty {
                        Text("No open commitments — you’re on top of it.").foregroundStyle(.secondary).padding(.vertical, 8)
                    }
                    VStack(spacing: 2) {
                        ForEach(shown, id: \.self) { item in
                            CommitmentRow(item: item, brief: brief, attention: attention, open: openAndClose)
                        }
                    }
                }
                .padding(20)
            }
            Divider()
            HStack {
                Text("Click a to-do to open it · ✓ done (archive) · later snoozes to tomorrow")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            .padding(12)
        }
        .frame(width: 600, height: 560)
        .task { await tasks.load() }
    }

    private func openAndClose(_ copy: MailCopy) {
        dismiss()
        open(copy)
    }

    private func heading(_ title: LocalizedStringKey, symbol: String, count: String, note: LocalizedStringKey) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Label(title, systemImage: symbol).font(.headline)
            Text(count).font(.callout.monospacedDigit()).foregroundStyle(.secondary)
            Text(note).font(.caption).foregroundStyle(.secondary)
        }
    }
}

private struct TaskRow: View {
    let task: API.Task
    let tasks: TasksModel
    let open: (MailCopy) -> Void

    var body: some View {
        let due = BriefWords.due(task.due, today: BriefWords.localDay())
        let copy = TasksModel.copy(of: task)
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Button {
                Task { await tasks.mark(task, done: !task.done) }
            } label: {
                Image(systemName: task.done ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(task.done ? AnyShapeStyle(.green) : AnyShapeStyle(.secondary))
            }
            .buttonStyle(.plain)
            .help(task.done ? "Not done yet" : "Done")
            .accessibilityLabel(task.done ? "Mark not done" : "Mark done")
            Button {
                if let copy { open(copy) }
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(task.title).strikethrough(task.done).foregroundStyle(task.done ? .secondary : .primary)
                    HStack(spacing: 4) {
                        if !due.words.isEmpty {
                            Text(due.words).foregroundStyle(due.late && !task.done ? .red : .secondary)
                        }
                        if let who = task.fromName ?? task.fromAddr { Text("· \(who)") }
                        if let subject = task.subject, !subject.isEmpty { Text("· \(String(subject.prefix(64)))") }
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(copy == nil)
            .help(copy == nil ? "That message is no longer here" : "Open the message")
            Button("Remove", systemImage: "trash") { Task { await tasks.remove(task) } }
                .labelStyle(.iconOnly)
                .buttonStyle(.borderless)
                .help("Remove this task")
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 8)
        .background(.quinary, in: RoundedRectangle(cornerRadius: 6))
    }
}

private struct CommitmentRow: View {
    let item: API.DigestItem
    let brief: BriefModel
    let attention: AttentionModel
    let open: (MailCopy) -> Void
    @Environment(\.openURL) private var openURL

    var body: some View {
        let copy = BriefModel.copy(of: item)
        let level = max(1, min(5, item.importance ?? 1))
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Button { Task { await brief.file(item, .archive) } } label: { Image(systemName: "circle") }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .help("Done — archive")
                .accessibilityLabel("Done")
            Button { open(copy) } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.action)
                    Text(verbatim: "\(item.fromName ?? item.fromAddr ?? "—") · \(String((item.subject.isEmpty ? String(localized: "(no subject)") : item.subject).prefix(64))) · p\(level)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Button("Later — snooze to tomorrow", systemImage: "clock") {
                Task { await attention.snooze([copy], .preset(.tomorrow)) }
            }
            .labelStyle(.iconOnly)
            .buttonStyle(.borderless)
            .help("Later — snooze to tomorrow")
            if let link = URL(string: item.link), item.link.hasPrefix("https://") {
                Button("Open Original", systemImage: "arrow.up.right.square") { openURL(link) }
                    .labelStyle(.iconOnly)
                    .buttonStyle(.borderless)
                    .help("Open original")
            }
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 8)
        .background(.quinary, in: RoundedRectangle(cornerRadius: 6))
    }
}

/// Your week: what Claude made of the week behind you, its numbers, and what still needs you.
struct ReviewSheet: View {
    let model: ReviewModel
    let open: (MailCopy) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Label("Your Week", systemImage: "calendar").font(.headline)
                if let review = model.review {
                    Text(verbatim: "· \(BriefWords.reviewRange(generated: review.generated, now: Date()))")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Regenerate", systemImage: "arrow.clockwise") { Task { await model.again() } }
                    .labelStyle(.iconOnly)
                    .help("Write the review again (runs the AI)")
                    .disabled(model.stage == .reading)
            }
            .padding(16)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    content
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider()
            HStack {
                Text("Click an item to open it").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            .padding(12)
        }
        .frame(width: 620, height: 620)
        .task { await model.open() }
    }

    @ViewBuilder private var content: some View {
        switch model.stage {
        case nil, .reading:
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Looking back over your week…")
            }
            .foregroundStyle(.secondary)
        case .aiOff:
            Text("AI is off, so there is no review: Claude writes it, and MailMind has no Anthropic API key.")
        case .limit:
            HStack {
                Text("Today’s AI limit is reached, so your review isn’t written.")
                Button("Write It") { Task { await model.again() } }.buttonStyle(.link)
            }
        case .failed(let why):
            HStack {
                Text(why)
                Button("Retry") { Task { await model.again() } }.buttonStyle(.link)
            }
        case .written(let review):
            ReviewBody(review: review, open: openAndClose, again: { Task { await model.again() } })
        }
    }

    private func openAndClose(_ copy: MailCopy) {
        dismiss()
        open(copy)
    }
}

/// A review written: the narrative, the week's numbers, what to start next week with, and what still needs you.
struct ReviewBody: View {
    let review: API.Review
    let open: (MailCopy) -> Void
    let again: () -> Void

    var body: some View {
        let stats = review.stats
        Text(review.narrative).textSelection(.enabled)
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 96), spacing: 8)], spacing: 8) {
            Stat(count: stats.sent, label: "sent")
            Stat(count: stats.received, label: "received")
            Stat(count: stats.needsYou, label: "need you", hot: stats.needsYou > 0)
            Stat(count: stats.waitingOn, label: "awaiting reply")
            if stats.replyLater > 0 { Stat(count: stats.replyLater, label: "reply-later") }
            if stats.cleaned > 0 { Stat(count: stats.cleaned, label: "cleaned up") }
            if stats.handled > 0 { Stat(count: stats.handled, label: "handled") }
        }
        if !review.next.isEmpty {
            section("Start Next Week With", symbol: "sparkles")
            ForEach(review.next, id: \.self) { SummaryNextStep($0) }
        }
        section(review.needs.isEmpty ? "Still Needs You" : "Still Needs You (\(review.needs.count))", symbol: "circle.fill")
        if review.needs.isEmpty {
            Text("Nothing important is unanswered.").foregroundStyle(.secondary)
        }
        ForEach(Array(review.needs.enumerated()), id: \.offset) { _, need in
            item(need.subject, detail: need.action.isEmpty ? need.who : "\(need.who) · \(need.action)",
                 copy: Self.copy(need.key, need.folder, need.uid, need.account))
        }
        if !review.meetings.isEmpty {
            section("Meetings and Deadlines", symbol: "calendar")
            ForEach(Array(review.meetings.enumerated()), id: \.offset) { _, meeting in
                item(meeting.summary,
                     detail: EventWhen.long(start: meeting.start, allDay: meeting.allDay) + (meeting.past ? " · past" : ""),
                     copy: Self.copy(meeting.key, meeting.folder, meeting.uid, meeting.account))
            }
        }
        if !review.waiting.isEmpty {
            section("You’re Waiting on a Reply", symbol: "hourglass")
            ForEach(Array(review.waiting.enumerated()), id: \.offset) { _, waiting in
                item(waiting.subject, detail: "to \(waiting.to.isEmpty ? "—" : waiting.to)",
                     copy: Self.copy(waiting.key, waiting.folder ?? "", waiting.uid, waiting.account))
            }
        }
        HStack(spacing: 4) {
            Text(verbatim: review.model == "fallback" ? String(localized: "summary from your data") : review.model.isEmpty ? "claude" : review.model)
            if !review.generated.isEmpty { Text(verbatim: "· \(review.generated.prefix(10))") }
            if review.cached { Text("· cached") }
            if review.stale {
                Text("· mail changed —").foregroundStyle(.orange)
                Button("refresh", action: again).buttonStyle(.link)
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.top, 8)
    }

    /// An item names its message's copy as it is now; one that names no message names none.
    static func copy(_ key: Int?, _ folder: String, _ uid: Int?, _ account: String) -> MailCopy? {
        guard let key, let uid, !folder.isEmpty else { return nil }
        return .named(key: key, folder: folder, uid: uid, account: account)
    }

    private func section(_ title: String, symbol: String) -> some View {
        Label(title, systemImage: symbol)
            .font(.callout.weight(.semibold))
            .foregroundStyle(.secondary)
            .padding(.top, 6)
    }

    private func item(_ title: String, detail: String, copy: MailCopy?) -> some View {
        Button {
            if let copy { open(copy) }
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).foregroundStyle(.primary)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
            .background(.quinary, in: RoundedRectangle(cornerRadius: 6))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(copy == nil)
    }
}

private struct Stat: View {
    let count: Int
    let label: LocalizedStringKey
    var hot = false

    var body: some View {
        VStack(spacing: 2) {
            Text(count, format: .number).font(.title2.weight(.semibold).monospacedDigit())
                .foregroundStyle(hot ? AnyShapeStyle(.orange) : AnyShapeStyle(.primary))
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(.quinary, in: RoundedRectangle(cornerRadius: 8))
        .accessibilityElement(children: .combine)
    }
}
#endif
