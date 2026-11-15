#if os(macOS)
import Observation
import SwiftUI

/// A bulk action said in plain words (`/api/ai/do-plan`, then `/api/ai/do-confirm`), as the page's "!…" plans one.
/// What a plan would touch is frozen by the server at the preview, and nothing happens until a person confirms it by
/// the plan's id; a destructive one takes a second press. A plan that isn't an action is a search: it acts on nothing.
@MainActor
@Observable
final class PlanModel {
    enum Doing: Equatable {
        case intro
        case planning(command: String)
        case failed(command: String, why: String)
        /// It couldn't be turned into a safe action, and names no search either.
        case noFilter(command: String)
        /// It reads as a search, and acts on nothing.
        case search(command: String, filter: String)
        case plan(command: String, API.PlanPreview, armed: Bool)
    }

    var doing: Doing?
    @ObservationIgnored private var seq = 0
    @ObservationIgnored let link: CoreLink
    @ObservationIgnored let notices: DeskNotices
    /// Read the lists again once a plan has acted.
    @ObservationIgnored var changed: () -> Void = {}

    init(link: CoreLink, notices: DeskNotices) {
        self.link = link
        self.notices = notices
    }

    /// The page's words for each verb, on its button.
    static func label(_ verb: API.PlanVerb) -> String {
        switch verb {
        case .archive: String(localized: "Archive")
        case .trash: String(localized: "Delete")
        case .junk: String(localized: "Mark junk")
        case .read: String(localized: "Mark read")
        case .unread: String(localized: "Mark unread")
        case .star: String(localized: "Star")
        case .snooze: String(localized: "Snooze")
        }
    }

    /// What it says once done.
    static func done(_ verb: API.PlanVerb) -> String {
        switch verb {
        case .archive: String(localized: "Archived")
        case .trash: String(localized: "Trashed")
        case .junk: String(localized: "Marked junk")
        case .snooze: String(localized: "Snoozed")
        case .read, .unread, .star: String(localized: "Updated")
        }
    }

    static func destructive(_ verb: API.PlanVerb) -> Bool {
        verb == .trash || verb == .junk
    }

    func start() {
        seq += 1
        doing = .intro
    }

    /// Plan `command`, and show what it would do.
    func plan(_ command: String) async {
        let command = command.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let routes = link.routes, !command.isEmpty else { return }
        seq += 1
        let mine = seq
        doing = .planning(command: command)
        let answer: API.AiDoPlanAnswer
        do {
            answer = try await routes.post(API.Post.AiDoPlan.self, API.PlanBody(cmd: command))
        } catch {
            guard mine == seq else { return }
            let why = DeskRefusal.isAIOff(error) ? String(localized: "AI is off")
                : DeskRefusal.isOverLimit(error) ? String(localized: "today’s AI limit is reached")
                : String(localized: "planner failed")
            doing = .failed(command: command, why: why)
            return
        }
        guard mine == seq else { return }
        switch answer {
        case .planPreview(let preview) where preview.intent == "act":
            doing = .plan(command: command, preview, armed: false)
            AccessibilityNotification.Announcement(
                "\(Self.label(preview.verb)) \(preview.count) messages? Review and confirm.").post()
        case .planPreview(let preview):
            doing = Self.asSearch(command, preview.filter)
        case .planIsASearch(let search):
            doing = Self.asSearch(command, search.filter)
        }
    }

    private static func asSearch(_ command: String, _ filter: String) -> Doing {
        let filter = filter.trimmingCharacters(in: .whitespacesAndNewlines)
        return filter.isEmpty ? .noFilter(command: command) : .search(command: command, filter: filter)
    }

    /// A destructive plan's first press arms it; the second confirms.
    func press() async {
        guard case .plan(let command, let preview, let armed) = doing else { return }
        if Self.destructive(preview.verb) && !armed {
            doing = .plan(command: command, preview, armed: true)
            return
        }
        await confirm(preview)
    }

    private func confirm(_ preview: API.PlanPreview) async {
        guard let routes = link.routes else { return }
        stop()
        let verb = preview.verb
        notices.say("\(Self.label(verb)) \(preview.capped ? preview.cap : preview.count)…")
        do {
            let done = try await routes.post(API.Post.AiDoConfirm.self,
                                             API.ConfirmBody(plan: preview.plan, undoGroup: newUndoGroup()))
            var said = ["\(Self.done(verb)) \(done.count)"]
            if done.failed > 0 { said.append(String(localized: "\(done.failed) failed")) }
            if done.gone > 0 { said.append(String(localized: "\(done.gone) had moved")) }
            notices.say(said.joined(separator: ", "), undoable: done.undoable)
        } catch {
            notices.say((error as? Problem)?.status == 410
                ? String(localized: "That preview has expired. Run the command again.")
                : String(localized: "Couldn’t finish that. Check what was done before trying again."))
        }
        changed()
    }

    func stop() {
        seq += 1
        doing = nil
    }
}

/// The sheet a bulk action passes through: the command, then what it would do and how many, then the confirmation.
struct PlanSheet: View {
    let model: PlanModel
    /// Shows a search's filter as the search it is, where the window has a search to show.
    let showSearch: ((String) -> Void)?
    @State private var command = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            switch model.doing {
            case .intro, nil:
                intro
            case .planning(let command):
                header
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Planning “\(command)”…")
                }
                .frame(maxWidth: .infinity, minHeight: 80)
                footer { Button("Cancel", role: .cancel) { model.stop() }.keyboardShortcut(.cancelAction) }
            case .failed(let command, let why):
                header
                Text("Couldn’t plan that: \(why)").foregroundStyle(.red)
                footer {
                    Button("Close", role: .cancel) { model.stop() }.keyboardShortcut(.cancelAction)
                    Button("Retry") { Task { await model.plan(command) } }.keyboardShortcut(.defaultAction)
                }
            case .noFilter:
                header
                Text("I couldn’t turn that into a **safe** bulk action. Try naming a scope *and* a verb — e.g. “archive newsletters older than a week”.")
                footer {
                    Button("Try Again") { model.start() }
                    Button("Close", role: .cancel) { model.stop() }.keyboardShortcut(.cancelAction)
                }
            case .search(_, let filter):
                header
                Text("That reads as a search, so it acts on nothing:")
                Text(filter).font(.body.monospaced()).textSelection(.enabled)
                footer {
                    Button("Close", role: .cancel) { model.stop() }.keyboardShortcut(.cancelAction)
                    if let showSearch {
                        Button("Show as Search") {
                            model.stop()
                            showSearch(filter)
                        }
                        .keyboardShortcut(.defaultAction)
                    }
                }
            case .plan(let command, let preview, let armed):
                PlanPreviewCard(command: command, preview: preview, armed: armed, model: model, showSearch: showSearch)
            }
        }
        .padding(20)
        .frame(width: 520)
    }

    private var header: some View {
        Label("Bulk Action", systemImage: "sparkles").font(.headline)
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            Text("Type a command in plain English and press Return — you’ll see exactly what it affects **before anything happens**.")
            TextField("archive newsletters older than a week", text: $command)
                .textFieldStyle(.roundedBorder)
                .onSubmit { Task { await model.plan(command) } }
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Self.examples, id: \.self) { example in
                    Button(example) { command = example }
                        .buttonStyle(.link)
                        .font(.callout)
                }
            }
            footer {
                Button("Cancel", role: .cancel) { model.stop() }.keyboardShortcut(.cancelAction)
                Button("Plan") { Task { await model.plan(command) } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
    }

    static let examples = [
        "archive newsletters older than a week", "mark everything from LinkedIn as read", "snooze all receipts until next week",
    ]

    private func footer<Buttons: View>(@ViewBuilder _ buttons: () -> Buttons) -> some View {
        HStack {
            Spacer()
            buttons()
        }
        .padding(.top, 4)
    }
}

/// What a plan would do: the verb and how many, what it matches, a sample, and the press that does it.
struct PlanPreviewCard: View {
    let command: String
    let preview: API.PlanPreview
    let armed: Bool
    let model: PlanModel
    let showSearch: ((String) -> Void)?

    var body: some View {
        let verb = preview.verb
        let danger = PlanModel.destructive(verb)
        let acting = preview.capped ? preview.cap : preview.count
        VStack(alignment: .leading, spacing: 10) {
            Label {
                Text("\(PlanModel.label(verb)) **\(preview.count)** messages?")
            } icon: {
                Image(systemName: danger ? "exclamationmark.triangle.fill" : "sparkles")
            }
            .font(.headline)
            .foregroundStyle(danger ? AnyShapeStyle(.red) : AnyShapeStyle(.primary))
            HStack(spacing: 4) {
                Text("matching")
                Text(preview.filter).font(.callout.monospaced())
                if verb == .snooze {
                    Text("· until \(preview.snoozePreset.rawValue.replacingOccurrences(of: "_", with: " "))")
                }
            }
            .font(.callout)
            .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(preview.sample.enumerated()), id: \.offset) { _, sample in
                    HStack(spacing: 8) {
                        Text(sample.from).fontWeight(.medium).frame(width: 130, alignment: .leading)
                        Text(sample.subject).lineLimit(1)
                        Spacer()
                        Text(sample.when).foregroundStyle(.secondary)
                    }
                    .font(.callout)
                    .lineLimit(1)
                }
                if preview.count > preview.sample.count {
                    Text("+ \(preview.count - preview.sample.count) more").font(.callout).foregroundStyle(.secondary)
                }
            }
            .padding(10)
            .background(.quinary, in: RoundedRectangle(cornerRadius: 8))
            if preview.capped {
                Label("Acts on the most recent \(preview.cap) of \(preview.count) — run “\(command)” again to continue.",
                      systemImage: "exclamationmark.circle")
                    .font(.callout)
            }
            if danger {
                Text(verb == .trash ? "Moves to Trash — reversible with Undo." : "Marks as junk — reversible with Undo.")
                    .font(.callout)
                    .foregroundStyle(.red)
            }
            if verb == .read || verb == .unread || verb == .star {
                Text("Not tracked by Undo — reverse it with the opposite command.").font(.callout).foregroundStyle(.secondary)
            }
            HStack {
                if let showSearch {
                    Button("Show as Search") {
                        model.stop()
                        showSearch(preview.filter)
                    }
                }
                Spacer()
                Button("Cancel", role: .cancel) { model.stop() }.keyboardShortcut(.cancelAction)
                Button(role: danger ? .destructive : nil) {
                    Task { await model.press() }
                } label: {
                    Text(armed ? String(localized: "Click Again to Confirm") : "\(PlanModel.label(verb)) \(acting)")
                }
                .keyboardShortcut(.defaultAction)
                .tint(danger ? .red : .accentColor)
            }
            .padding(.top, 4)
        }
    }
}
#endif
