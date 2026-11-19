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
            case .reviewMissing:   // none yet: you opened it, so write it, asking at the limit
                stage = .written(try await routes.post(API.Post.Review.self, API.NoBody(), asking: link.asksOverLimit))
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
            stage = .written(try await routes.post(API.Post.ReviewRefresh.self, API.NoBody(),
                                                   asking: link.asksOverLimit))
        } catch {
            stage = Self.stage(error)
        }
    }

    static func stage(_ error: any Error) -> Stage {
        DeskRefusal.isAIOff(error) ? .aiOff : DeskRefusal.isOverLimit(error) ? .limit
            : .failed(DeskRefusal.words(error, else: String(localized: "Couldn’t build your review.")))
    }
}

/// Commitments: the tasks you confirmed, then the AI's to-dos pulled from your mail, as one checkable agenda on paper.
struct CommitmentsSheet: View {
    let tasks: TasksModel
    let brief: BriefModel
    let attention: AttentionModel
    let open: (MailCopy) -> Void
    @Environment(\.dismiss) private var dismiss
    @Environment(\.meadow) private var meadow

    var body: some View {
        let all = BriefWords.commitments(brief.items)
        let shown = Array(all.prefix(BriefWords.commitCap))
        VStack(alignment: .leading, spacing: 0) {
            DeskSheetHead(title: String(localized: "Commitments"), icon: .todo)
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if !tasks.tasks.isEmpty {
                        heading(String(localized: "Your tasks"), icon: .checkCircle, count: "\(tasks.open)",
                                note: String(localized: "the ones you confirmed from your mail"))
                        VStack(spacing: 2) {
                            ForEach(tasks.tasks, id: \.id) { task in TaskRow(task: task, tasks: tasks, open: openAndClose) }
                        }
                        .padding(.bottom, 8)
                    }
                    heading(String(localized: "From your mail"), icon: .wind,
                            count: all.count > BriefWords.commitCap ? "\(all.count) · top \(BriefWords.commitCap)"
                                : "\(all.count)",
                            note: String(localized: "the AI’s to-dos pulled from your mail"))
                    if shown.isEmpty {
                        Text("No open commitments — you’re on top of it.")
                            .font(.meadow(.ui, 13.5))
                            .foregroundStyle(meadow.ink3)
                            .padding(.vertical, 8)
                    }
                    VStack(spacing: 2) {
                        ForEach(shown, id: \.self) { item in
                            CommitmentRow(item: item, brief: brief, attention: attention, open: openAndClose)
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 16)
            }
            DeskSheetFoot(words: Self.hint, done: { dismiss() })
        }
        .frame(width: 600, height: 560)
        .background(meadow.paper)
        .task { await tasks.load() }
    }

    private static let hint = String(localized: """
        Click a to-do to open it · ✓ done (archive) · later snoozes to tomorrow
        """)

    private func openAndClose(_ copy: MailCopy) {
        dismiss()
        open(copy)
    }

    private func heading(_ title: String, icon: MeadowIcon, count: String, note: String) -> some View {
        HStack(spacing: 8) {
            MeadowIconView(icon, size: 15).foregroundStyle(meadow.ink3)
            MeadowSectionLabel(title)
            Text(count).font(.meadow(.ui, 12, .heavy)).monospacedDigit().foregroundStyle(meadow.ink3)
            Text(note).font(.meadow(.ui, 12.5)).foregroundStyle(meadow.ink3)
        }
    }
}

/// A sheet's head on paper: an icon and a title in the display face, and what goes beside them.
struct DeskSheetHead<Trailing: View>: View {
    let title: String
    let icon: MeadowIcon
    @ViewBuilder var trailing: Trailing
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            MeadowIconView(icon, size: 18).foregroundStyle(meadow.ink2)
            Text(title)
                .font(.meadow(.display, 19, .bold))
                .foregroundStyle(meadow.ink)
                .accessibilityAddTraits(.isHeader)
            trailing
        }
        // Leading, with no spacer of its own: a spacer in what goes beside the title takes all the room left.
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 20)
        .padding(.top, 18)
        .padding(.bottom, 12)
        .overlay(alignment: .bottom) { Rectangle().fill(meadow.line).frame(height: 1) }
    }
}

extension DeskSheetHead where Trailing == EmptyView {
    init(title: String, icon: MeadowIcon) {
        self.init(title: title, icon: icon) { EmptyView() }
    }
}

/// A sheet's foot: a hint in the quiet ink and Done.
struct DeskSheetFoot: View {
    let words: String
    let done: () -> Void
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 10) {
            Text(words).font(.meadow(.ui, 12)).foregroundStyle(meadow.ink3)
            Spacer(minLength: 8)
            Button("Done", action: done)
                .meadowButton()
                .keyboardShortcut(.cancelAction)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
    }
}

/// What the window last said, on a sheet over it: a sheet covers the window's foot, where its toast stands, and holds
/// the window's Undo out of reach while it is up, so what is done on the sheet is said on the sheet, with its Undo,
/// as the page's toasts show beside its drawer. The window's toast, under the sheet, has already been read out.
struct DeskSheetToast: View {
    let mailbox: Mailbox?

    var body: some View {
        if let mailbox, let toast = mailbox.toast {
            MeadowToast(icon: MailboxToast.icon(toast.text, refused: toast.refused), text: Text(toast.text),
                        detail: toast.detail) {
                if toast.undo != nil {
                    MeadowToastButton(title: String(localized: "Undo"), icon: .undo) { mailbox.undo() }
                }
            }
            .id(toast.id)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }
}

/// A row on a sheet: raised paper while hovered, as the list's rows are.
struct DeskSheetRow<Content: View>: View {
    @ViewBuilder let content: Content
    @Environment(\.meadow) private var meadow
    @State private var hovering = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            content
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 10)
        .background(RoundedRectangle(cornerRadius: 10).fill(hovering ? meadow.paper2 : .clear))
        .onHover { hovering = $0 }
    }
}

private struct TaskRow: View {
    let task: API.Task
    let tasks: TasksModel
    let open: (MailCopy) -> Void
    @Environment(\.meadow) private var meadow

    var body: some View {
        let due = BriefWords.due(task.due, today: BriefWords.localDay())
        let copy = TasksModel.copy(of: task)
        DeskSheetRow {
            DeskCheckbox(on: task.done, label: task.done ? String(localized: "Mark not done")
                         : String(localized: "Mark done")) {
                Task { await tasks.mark(task, done: !task.done) }
            }
            Button {
                if let copy { open(copy) }
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(task.title)
                        .font(.meadow(.ui, 14, .semibold))
                        .strikethrough(task.done, color: meadow.ink3)
                        .foregroundStyle(task.done ? meadow.ink3 : meadow.ink)
                    HStack(spacing: 4) {
                        if !due.words.isEmpty {
                            Text(due.words)
                                .foregroundStyle(due.late && !task.done ? meadow.rust : meadow.ink3)
                                .fontWeight(due.late && !task.done ? .bold : .regular)
                        }
                        let rest = [task.fromName ?? task.fromAddr, task.subject.map { String($0.prefix(64)) }]
                            .compactMap { $0 }.filter { !$0.isEmpty }
                        if !rest.isEmpty {
                            Text(verbatim: (due.words.isEmpty ? "" : "· ") + rest.joined(separator: " · "))
                        }
                    }
                    .font(.meadow(.ui, 12.5))
                    .foregroundStyle(meadow.ink3)
                    .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .deskTitle()
            .disabled(copy == nil)
            .help(copy == nil ? "That message is no longer here" : "Open the message")
            Button { Task { await tasks.remove(task) } } label: { MeadowIconView(.trash, size: 15) }
                .meadowButton(.quiet, del: true, small: true, icon: true)
                .help("Remove this task")
                .accessibilityLabel("Remove")
        }
    }
}

private struct CommitmentRow: View {
    let item: API.DigestItem
    let brief: BriefModel
    let attention: AttentionModel
    let open: (MailCopy) -> Void
    @Environment(\.openURL) private var openURL
    @Environment(\.meadow) private var meadow

    var body: some View {
        let copy = BriefModel.copy(of: item)
        let level = max(1, min(5, item.importance ?? 1))
        let subject = item.subject.isEmpty ? String(localized: "(no subject)") : item.subject
        DeskSheetRow {
            DeskCheckbox(on: false, label: String(localized: "Done — archive")) {
                Task { await brief.file(item, .archive) }
            }
            Button { open(copy) } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.action)
                        .font(.meadow(.ui, 14, .semibold))
                        .foregroundStyle(meadow.ink)
                    let who = item.fromName ?? item.fromAddr ?? "—"
                    Text(verbatim: "\(who) · \(String(subject.prefix(64))) · p\(level)")
                        .font(.meadow(.ui, 12.5))
                        .foregroundStyle(meadow.ink3)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .deskTitle()
            Button { Task { await attention.snooze([copy], .preset(.tomorrow)) } } label: {
                MeadowIconView(.clock, size: 15)
            }
            .meadowButton(.quiet, small: true, icon: true)
            .help("Later — snooze to tomorrow")
            .accessibilityLabel("Later — snooze to tomorrow")
            if let link = URL(string: item.link), item.link.hasPrefix("https://") {
                Button { openURL(link) } label: { MeadowIconView(.external, size: 15) }
                    .meadowButton(.quiet, small: true, icon: true)
                    .help("Open original")
                    .accessibilityLabel("Open Original")
            }
        }
    }
}

/// Your week: what the AI made of the week behind you, its numbers, and what still needs you.
struct ReviewSheet: View {
    let model: ReviewModel
    let open: (MailCopy) -> Void
    @Environment(\.dismiss) private var dismiss
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            DeskSheetHead(title: String(localized: "Your week"), icon: .cal) {
                if let review = model.review {
                    Text(verbatim: "· \(BriefWords.reviewRange(generated: review.generated, now: Date()))")
                        .font(.meadow(.ui, 13.5))
                        .foregroundStyle(meadow.ink3)
                }
                Spacer(minLength: 8)
                Button { Task { await model.again() } } label: {
                    MeadowIconView(.refresh, size: 15)
                    Text("Write again")
                }
                .meadowButton(.quiet, small: true)
                .help("Write the review again (runs the AI)")
                .disabled(model.stage == .reading)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    content
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            DeskSheetFoot(words: String(localized: "Click an item to open it"), done: { dismiss() })
        }
        .frame(width: 620, height: 620)
        .background(meadow.paper)
        .task { await model.open() }
    }

    @ViewBuilder private var content: some View {
        switch model.stage {
        case nil, .reading:
            DeskWaitingBars(lines: 4, saying: "Looking back over your week…")
        case .aiOff:
            Text("AI is off, so there is no review: the AI writes it. Settings › AI says why, and turns it on.")
                .font(.meadow(.ui, 13.5))
                .foregroundStyle(meadow.ink2)
        case .limit:
            HStack(spacing: 8) {
                Text("Today’s AI limit is reached, so your review isn’t written.")
                    .font(.meadow(.ui, 13.5))
                    .foregroundStyle(meadow.ink2)
                Button("Write it") { Task { await model.again() } }.deskLink()
            }
        case .failed(let why):
            HStack(spacing: 8) {
                Text(why).font(.meadow(.ui, 13.5)).foregroundStyle(meadow.rust)
                Button("Retry") { Task { await model.again() } }.deskLink()
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

/// A review written: the narrative in the AI's voice, the week's numbers, what to start next week with, and what
/// still needs you.
struct ReviewBody: View {
    let review: API.Review
    let open: (MailCopy) -> Void
    let again: () -> Void
    @Environment(\.meadow) private var meadow

    var body: some View {
        let stats = review.stats
        DeskCard(look: .note) {
            MeadowAIMark(String(localized: "Your week, in a few lines"))
            Text(review.narrative)
                .font(.meadow(.hand, 17, .semibold))
                .foregroundStyle(meadow.moss)
                .lineSpacing(MeadowFace.hand.lineSpacing(size: 17, lineHeight: 1.6, weight: .semibold))
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        }
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
            section(String(localized: "Start next week with"), icon: .wind)
            ForEach(review.next, id: \.self) { step in
                // The AI's words, so in moss, as the brief's next steps are.
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    MeadowIconView(.arrowRight, size: 14)
                    Text(step).textSelection(.enabled)
                }
                .font(.meadow(.ui, 13.5, .semibold))
                .foregroundStyle(meadow.moss)
            }
        }
        section(review.needs.isEmpty ? String(localized: "Still needs you")
                : String(localized: "Still needs you · \(review.needs.count)"), icon: .inbox)
        if review.needs.isEmpty {
            Text("Nothing important is unanswered.").font(.meadow(.ui, 13.5)).foregroundStyle(meadow.ink3)
        }
        ForEach(Array(review.needs.enumerated()), id: \.offset) { _, need in
            item(need.subject, detail: need.action.isEmpty ? need.who : "\(need.who) · \(need.action)",
                 copy: Self.copy(need.key, need.folder, need.uid, need.account))
        }
        if !review.meetings.isEmpty {
            section(String(localized: "Meetings and deadlines"), icon: .cal)
            ForEach(Array(review.meetings.enumerated()), id: \.offset) { _, meeting in
                item(meeting.summary,
                     detail: EventWhen.long(start: meeting.start, allDay: meeting.allDay) + (meeting.past ? " · past" : ""),
                     copy: Self.copy(meeting.key, meeting.folder, meeting.uid, meeting.account))
            }
        }
        if !review.waiting.isEmpty {
            section(String(localized: "You’re waiting on a reply"), icon: .hourglass)
            ForEach(Array(review.waiting.enumerated()), id: \.offset) { _, waiting in
                item(waiting.subject, detail: "to \(waiting.to.isEmpty ? "—" : waiting.to)",
                     copy: Self.copy(waiting.key, waiting.folder ?? "", waiting.uid, waiting.account))
            }
        }
        HStack(spacing: 4) {
            Text(verbatim: review.model == "fallback" ? String(localized: "summary from your data")
                 : review.model)
            if !review.generated.isEmpty { Text(verbatim: "· \(review.generated.prefix(10))") }
            if review.cached { Text("· cached") }
            if review.stale {
                Text("· mail changed —").foregroundStyle(meadow.sunInk)
                Button("refresh", action: again).deskLink()
            }
        }
        .font(.meadow(.ui, 12))
        .foregroundStyle(meadow.ink3)
        .padding(.top, 8)
    }

    /// An item names its message's copy as it is now; one that names no message names none.
    static func copy(_ key: Int?, _ folder: String, _ uid: Int?, _ account: String) -> MailCopy? {
        guard let key, let uid, !folder.isEmpty else { return nil }
        return .named(key: key, folder: folder, uid: uid, account: account)
    }

    private func section(_ title: String, icon: MeadowIcon) -> some View {
        HStack(spacing: 8) {
            MeadowIconView(icon, size: 15).foregroundStyle(meadow.ink3)
            MeadowSectionLabel(title)
        }
        .padding(.top, 10)
    }

    private func item(_ title: String, detail: String, copy: MailCopy?) -> some View {
        Button {
            if let copy { open(copy) }
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.meadow(.ui, 14, .semibold)).foregroundStyle(meadow.ink)
                Text(detail).font(.meadow(.ui, 12.5)).foregroundStyle(meadow.ink3)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(RoundedRectangle(cornerRadius: 10).fill(meadow.paperRaised))
            .overlay { RoundedRectangle(cornerRadius: 10).strokeBorder(meadow.line) }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(copy == nil)
    }
}

/// One of the week's numbers (`.money` at a smaller size): the count in the display face, what it counts under it,
/// what needs you in sunlight.
private struct Stat: View {
    let count: Int
    let label: LocalizedStringKey
    var hot = false
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(spacing: 2) {
            Text(count, format: .number)
                .font(.meadow(.display, 22, .bold))
                .monospacedDigit()
                .foregroundStyle(hot ? meadow.sunInk : meadow.ink)
            Text(label).font(.meadow(.ui, 12)).foregroundStyle(meadow.ink3)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(RoundedRectangle(cornerRadius: 12).fill(hot ? meadow.sunWash : meadow.paperRaised))
        .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(hot ? meadow.sun.opacity(0.45) : meadow.line) }
        .accessibilityElement(children: .combine)
    }
}
#endif
