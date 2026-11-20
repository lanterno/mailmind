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
            answer = try await routes.post(API.Post.AiDoPlan.self, API.PlanBody(cmd: command),
                                           asking: link.asksOverLimit)
        } catch {
            guard mine == seq else { return }
            let why = DeskRefusal.isAIOff(error) ? DeskRefusal.aiOff(error)
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

    /// Return or the button on a plan on screen: a destructive plan's first press arms it; otherwise `closing` closes
    /// what shows it, and the plan as it was shown runs.
    func press(closing: () -> Void) {
        guard case .plan(let command, let preview, let armed) = doing else { return }
        if Self.destructive(preview.verb) && !armed {
            doing = .plan(command: command, preview, armed: true)
            return
        }
        closing()
        Task { await confirm(preview) }
    }

    /// Take back a destructive plan's first press, so the next press arms it again: what is typed changed, or what
    /// showed it went.
    func disarm() {
        guard case .plan(let command, let preview, true) = doing else { return }
        doing = .plan(command: command, preview, armed: false)
    }

    /// The command being planned or shown; none before one is said.
    var command: String? {
        switch doing {
        case .planning(let command)?, .failed(let command, _)?, .noFilter(let command)?, .search(let command, _)?,
             .plan(let command, _, _)?:
            command
        case .intro?, nil:
            nil
        }
    }

    /// The plan for what a person typed, if it is the one on screen: its words, trimmed, are the command planned.
    func shown(for words: String) -> Doing? {
        let words = words.trimmingCharacters(in: .whitespacesAndNewlines)
        return command != nil && command == words ? doing : nil
    }

    func confirm(_ preview: API.PlanPreview) async {
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
            notices.say(said.joined(separator: ", "), undo: done.undoable ? Self.label(verb) : nil)
        } catch {
            notices.say(ReadOnlyWords.said(error, else: (error as? Problem)?.status == 410
                ? String(localized: "That preview has expired. Run the command again.")
                : String(localized: "Couldn’t finish that. Check what was done before trying again.")), refused: true)
        }
        changed()
    }

    func stop() {
        seq += 1
        doing = nil
    }
}

/// A bulk action in the palette's `!` mode, on the mockup's preview card (`.preview-bulk`): what to say while nothing
/// is planned, the planning, what went wrong, a plan that reads as a search, and the plan itself with its press.
/// Nothing is planned until Return, and nothing runs until the press.
struct PlanCard: View {
    let model: PlanModel
    /// What is typed after the `!`.
    let words: String
    /// Shows a search's filter as the search it is, where the window has a search to show.
    let showSearch: ((String) -> Void)?
    /// The key that undoes the batch: Z, or ⌘Z with single keys off.
    var undoKey = "Z"
    let close: @MainActor () -> Void

    var body: some View {
        // A first press counts only for the words it was made on, while they are on screen: an edit, a mode
        // left or the palette gone takes it back.
        card
            .onChange(of: words) { model.disarm() }
            .onDisappear { model.disarm() }
    }

    @ViewBuilder private var card: some View {
        switch model.shown(for: words) {
        case .planning(let command)?:
            PlanCardBox {
                MeadowThinking(text: String(localized: "Planning “\(command)”…"))
            }
        case .failed(let command, let why)?:
            PlanCardBox {
                PlanCardTitle(Text("Couldn’t plan that"))
                PlanCardNote(String(localized: "The planner said: \(why). Nothing was changed."))
                PlanCardActions {
                    Button("Close", action: close).meadowButton(.quiet)
                    Button("Retry") { Task { await model.plan(command) } }.meadowButton()
                }
            }
        case .noFilter(let command)?:
            PlanCardBox {
                PlanCardTitle(Text("That isn’t a safe action yet"))
                PlanCardNote(String(localized: """
                    MailMind couldn’t turn “\(command)” into a bulk action it trusts. Name a scope and a verb, as in \
                    “archive newsletters older than a week”. Nothing was changed.
                    """))
                PlanCardActions {
                    Button("Close", action: close).meadowButton(.quiet)
                }
            }
        case .search(_, let filter)?:
            PlanCardBox {
                PlanCardTitle(Text("That reads as a search"))
                PlanCardFilter(filter: filter)
                PlanCardNote(String(localized: "A search acts on nothing. Nothing was changed."))
                PlanCardActions {
                    Button("Close", action: close).meadowButton(.quiet)
                    if let showSearch {
                        Button("Search Instead") {
                            close()
                            showSearch(filter)
                        }
                        .meadowButton(.primary)
                    }
                }
            }
        case .plan(let command, let preview, let armed)?:
            PlanPreviewCard(command: command, preview: preview, armed: armed, model: model, showSearch: showSearch,
                            undoKey: undoKey, close: close)
        case .intro?, nil:
            PlanCardBox {
                MeadowAIMark(String(localized: "Act on many"))
                PlanCardTitle(Text(words.isEmpty ? String(localized: "Say what to do with which mail")
                    : String(localized: "Press ↵ to preview “\(words)”")))
                PlanCardNote(String(localized: """
                    You’ll see what it would touch and how many before anything happens, as in “archive newsletters \
                    older than a week”.
                    """))
            }
        }
    }
}

/// What a plan would do (`.preview-bulk`): the verb and how many, what it matches, a sample, what Undo can do, and the
/// press that does it, a destructive one's in rust and taken twice.
struct PlanPreviewCard: View {
    let command: String
    let preview: API.PlanPreview
    let armed: Bool
    let model: PlanModel
    let showSearch: ((String) -> Void)?
    var undoKey = "Z"
    let close: @MainActor () -> Void
    @Environment(\.meadow) private var meadow

    var body: some View {
        let verb = preview.verb
        let danger = PlanModel.destructive(verb)
        let acting = preview.capped ? preview.cap : preview.count
        PlanCardBox {
            MeadowAIMark(String(localized: "Preview — nothing has run yet"))
            PlanCardTitle(Self.title(preview))
            PlanCardFilter(filter: preview.filter)
            VStack(spacing: 2) {
                ForEach(Array(preview.sample.enumerated()), id: \.offset) { _, sample in
                    PlanSampleRow(words: "\(sample.from) — \(sample.subject)", count: sample.when)
                }
                if preview.count > preview.sample.count {
                    PlanSampleRow(words: String(localized: "…and \(preview.count - preview.sample.count) more"),
                                  count: "")
                }
            }
            .padding(.top, 12)
            if preview.capped {
                PlanCardNote(String(localized: """
                    This runs on the most recent \(preview.cap) of \(preview.count). Run “\(command)” again for the \
                    rest.
                    """))
            }
            PlanCardActions(note: undoNote(verb)) {
                if let showSearch {
                    Button("Show as Search") {
                        close()
                        showSearch(preview.filter)
                    }
                    .meadowButton(.quiet)
                }
                Button("Cancel", action: close).meadowButton(.quiet)
                Button {
                    model.press(closing: close)
                } label: {
                    Text(armed ? String(localized: "Click again to confirm")
                         : String(localized: "\(PlanModel.label(verb)) \(acting) messages"))
                }
                .meadowButton(danger ? .danger : .primary)
            }
        }
    }

    /// The card's title: the verb, how many in heavy type, and when a snooze brings them back.
    static func title(_ preview: API.PlanPreview) -> Text {
        let many = Text(String(localized: "\(preview.count) messages")).fontWeight(.black)
        let verb = PlanModel.label(preview.verb)
        if preview.verb == .snooze {
            let until = Snoozing.words(preview.snoozePreset).lowercased()
            return Text("\(verb) \(many) until \(until)")
        }
        return Text("\(verb) \(many)")
    }

    /// What Undo can do for it, with its key where one undoes it.
    private func undoNote(_ verb: API.PlanVerb) -> AnyView {
        let first: String
        switch verb {
        case .read, .unread, .star:
            return AnyView(Text("Undo doesn’t track this: the opposite command reverses it."))
        case .trash: first = String(localized: "Moves to Trash. One")
        case .junk: first = String(localized: "Marks as junk. One")
        case .archive, .snooze: first = String(localized: "Nothing is deleted. One")
        }
        return AnyView(HStack(spacing: 4) {
            Text(first)
            MeadowKey(undoKey)
            Text("undoes the batch.")
        }
        .accessibilityElement(children: .combine))
    }
}

/// The preview card's box (`.preview-bulk`): paper inside a stronger hairline, radius 14, 6 in from the palette.
struct PlanCardBox<Content: View>: View {
    @ViewBuilder let content: () -> Content
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) { content() }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 16)
            .padding(.horizontal, 18)
            .background(RoundedRectangle(cornerRadius: 14).fill(meadow.paper))
            .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(meadow.line2, lineWidth: 1))
            .padding(6)
    }
}

/// The card's title (`.preview-bulk h3`): 17 point, bold, 10 under the mark above it.
private struct PlanCardTitle: View {
    let text: Text
    @Environment(\.meadow) private var meadow

    init(_ text: Text) {
        self.text = text
    }

    var body: some View {
        text
            .font(.meadow(.ui, 17, .bold))
            .foregroundStyle(meadow.ink)
            .lineSpacing(MeadowFace.ui.lineSpacing(size: 17, lineHeight: 1.35, weight: .bold))
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 10)
            .accessibilityAddTraits(.isHeader)
    }
}

/// What the plan matches, as the server wrote its filter (`.filter`).
private struct PlanCardFilter: View {
    let filter: String
    @Environment(\.meadow) private var meadow

    var body: some View {
        Text(verbatim: filter)
            .font(.meadow(.mono, 12.5, .semibold))
            .foregroundStyle(meadow.ink2)
            .textSelection(.enabled)
            .padding(.vertical, 6)
            .padding(.horizontal, 9)
            .background(RoundedRectangle(cornerRadius: 8).fill(meadow.paper3))
            .padding(.top, 8)
            .accessibilityLabel("Matching \(filter)")
    }
}

/// One of the messages it would touch (`.preview-bulk li`): who and what on the left, the day on the right.
private struct PlanSampleRow: View {
    let words: String
    let count: String
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 12) {
            Text(words).lineLimit(1)
            Spacer(minLength: 0)
            Text(count).monospacedDigit()
        }
        .font(.meadow(.ui, 13))
        .foregroundStyle(meadow.ink)
        .padding(.vertical, 6)
        .padding(.horizontal, 8)
        .background(RoundedRectangle(cornerRadius: 8).fill(meadow.paper2))
        .accessibilityElement(children: .combine)
    }
}

/// A quieter line on the card.
private struct PlanCardNote: View {
    let text: String
    @Environment(\.meadow) private var meadow

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.meadow(.ui, 13))
            .foregroundStyle(meadow.ink3)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 8)
    }
}

/// The card's foot (`.acts`): a quiet note on the left, its buttons on the right.
private struct PlanCardActions<Buttons: View>: View {
    var note: AnyView?
    @ViewBuilder let buttons: () -> Buttons
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 8) {
            if let note {
                note
                    .font(.meadow(.ui, 12.5))
                    .foregroundStyle(meadow.ink3)
                    .lineLimit(2)
            }
            Spacer(minLength: 8)
            buttons()
        }
        .padding(.top, 14)
    }
}
#endif
