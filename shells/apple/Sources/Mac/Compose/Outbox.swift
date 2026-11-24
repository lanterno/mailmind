#if os(macOS)
import Observation
import SwiftUI

/// The tray the top bar's outbox opens, as the page's `compose/Outbox.tsx`: changes waiting for a server, drafts
/// waiting to reach theirs, and what the outbox holds, each with its way out.
@MainActor
@Observable
final class OutboxTray {
    private(set) var items: [API.OutboxItem] = []
    private(set) var drafts: [API.WaitingDraft] = []
    private(set) var held: [API.PendingChange] = []
    private(set) var waiting = 0
    private(set) var stuck = 0
    @ObservationIgnored private let routes: any Routes
    @ObservationIgnored private let say: (String) -> Void
    /// How many reads were begun: only the newest one's answers are shown, whichever comes back last.
    @ObservationIgnored private var reads = 0

    init(routes: any Routes, say: @escaping (String) -> Void) {
        self.routes = routes
        self.say = say
    }

    /// What is going: queued or sending, changes waiting, and drafts waiting.
    var active: Int { items.filter { $0.state == "queued" || $0.state == "sending" }.count + waiting + drafts.count }
    var failed: Int { items.filter { $0.state == "error" }.count + stuck }

    func load() async {
        reads += 1
        let read = reads
        let outbox = try? await routes.get(API.Get.Outbox.self, ["limit": "50"])
        let pending = try? await routes.get(API.Get.Pending.self, [:])
        guard read == reads else { return }
        if let outbox {
            items = outbox.items
            drafts = outbox.drafts
        }
        if let pending {
            held = pending.items
            waiting = pending.waiting
            stuck = pending.stuck
        }
    }

    // MARK: What each row can do

    private static let ops = ["trash": "Move to Trash", "archive": "Archive", "junk": "Mark as junk", "move": "Move"]
    private static let flags = ["seen": ("Mark unread", "Mark read"), "flagged": ("Remove star", "Star"),
                                "answered": ("Mark unanswered", "Mark answered")]
    // A folder_gone change was given back because another app deleted or renamed its folder: no retry undoes that,
    // and the server refused nothing.
    private static let why = ["refused": "the server refused it", "no_folder": "no folder to move it to",
                              "folder_gone": "its folder was deleted or renamed in another app",
                              "account removed": "its account is gone"]

    static func whatItDoes(_ change: API.PendingChange) -> String {
        if change.op == "flag" {
            guard let first = change.flags?.sorted(by: { $0.key < $1.key }).first,
                  let words = flags[first.key] else { return "Change" }
            return first.value ? words.1 : words.0
        }
        if change.op == "move", let dest = change.dest { return "Move to \(dest)" }
        return ops[change.op] ?? "Change"
    }

    /// Why a change waits. Read only queues nothing a person does: what waits while it is on, a run that wrote to the
    /// servers queued earlier, and it goes on the next such run.
    static func why(_ change: API.PendingChange, readOnly: Bool) -> String {
        if change.state == "error" { return why[change.error ?? ""] ?? "couldn’t reach the server" }
        if readOnly { return "waits for a full run" }
        return change.attempts > 0 ? "tried \(change.attempts)×, trying again" : "once the server is back"
    }

    /// What a draft saved while read only was on is: kept on this Mac, until it is saved again with read only off.
    static let kept = String(localized: "Kept on this Mac")

    /// Why a draft waits: kept on this Mac, or on its way to its server, or, while read only is on, queued earlier for
    /// the next run that writes.
    static func why(_ draft: API.WaitingDraft, readOnly: Bool) -> String {
        if draft.kept { return kept }
        let goes = draft.discarding ? "leaves the server" : "reaches the server"
        if readOnly { return "\(goes) on a full run" }
        return draft.attempts > 0 ? "tried \(draft.attempts)×, trying again" : "\(goes) once it’s back"
    }

    static func label(_ item: API.OutboxItem) -> String {
        item.scheduled ? "scheduled" : item.retrying ? "retrying" : item.unsure ? "unsure" : item.state
    }

    /// What a cancelled send says: where its words are, when MailMind keeps its draft, which cancelling leaves in
    /// Drafts to send another time.
    static func cancelled(_ item: API.OutboxItem?) -> String {
        item?.draftId == nil ? String(localized: "Send cancelled") : String(localized: "Send cancelled — kept in Drafts")
    }

    /// Said of a send that waits for its time: MailMind sends only while it is open.
    static let whileOpen = String(localized: "goes while MailMind is open")

    /// A send's line under its subject: who it is to, when a scheduled one goes and that it goes only while MailMind
    /// is open, and what went wrong.
    static func detail(_ item: API.OutboxItem) -> String {
        let to = item.to.joined(separator: ", ") + (item.bccCount > 0 ? " +\(item.bccCount) bcc" : "")
        let when = item.scheduled ? " · \(SendTimes.words(item.nextAttemptAt)) · \(whileOpen)" : ""
        return "to \(to)\(when)\(item.error.map { " · \($0)" } ?? "")"
    }

    func act(_ what: String, _ id: Int) async {
        do {
            if what == "retry" {
                _ = try await routes.post(API.Post.PendingRetry.self, API.NamesAPending(id: id))
            } else {
                _ = try await routes.post(API.Post.PendingDiscard.self, API.NamesAPending(id: id))
            }
            say(what == "retry" ? "Trying again…" : "Discarded: it stays as it was")
        } catch {
            say(ReadOnlyWords.said(error, else: (error as? Problem)?.status == 409
                ? "It’s being tried now. Try again in a moment" : "It has already settled"))
        }
        await load()
    }

    func discardDraft(_ id: Int) async {
        do {
            _ = try await routes.post(API.Post.Draft.self, API.DraftBody(delete: true, draftId: id))
            say("Draft discarded")
        } catch {
            say(ReadOnlyWords.said(error, else: "Couldn’t discard it"))
        }
        await load()
    }

    func sendNow(_ id: Int) async {
        do {
            _ = try await routes.post(API.Post.OutboxSendnow.self, API.NamesASend(id: id))
            say("Sending now…")
        } catch {
            say(ReadOnlyWords.said(error, else: "Couldn’t send now"))
        }
        await later()
    }

    func cancel(_ id: Int) async {
        let item = items.first { $0.id == id }
        do {
            _ = try await routes.post(API.Post.OutboxCancel.self, API.NamesASend(id: id))
            say(Self.cancelled(item))
        } catch {
            say((error as? Problem)?.isRefusal == true ? "Too late to cancel" : "Cancel failed")
        }
        await later()
    }

    func retry(_ id: Int) async {
        do {
            let retried = try await routes.post(API.Post.OutboxRetry.self, API.NamesASend(id: id))
            say(retried.gone == true ? "It had gone out, so it wasn’t sent again" : "Retrying…")
        } catch {
            say(ReadOnlyWords.said(error, else: "Retry failed"))
        }
        await later()
    }

    private func later() async {
        try? await Task.sleep(for: .milliseconds(300))
        await load()
    }
}

extension API.PendingChange: Identifiable {}
extension API.WaitingDraft: Identifiable {}
extension API.OutboxItem: Identifiable {}

/// The top bar's outbox: shown while something is going or failed, with how many. Its tray is the composing model's,
/// read from launch: a task hung on the button would never run while nothing shows it.
struct OutboxButton: View {
    var composing = Composing.shared
    @State private var shown = false

    var body: some View {
        if let tray = composing.outbox, tray.active + tray.failed > 0 || shown {
            // In the top bar, a button beside the light's: the outbox's icon, or the alert's while one failed, and
            // how many wait.
            let count = Self.badge(tray.active + tray.failed)
            Button {
                shown.toggle()
            } label: {
                MeadowIconView(tray.failed > 0 ? .alert : .outbox)
                if let count { Text(verbatim: count).monospacedDigit() }
            }
            .meadowButton()
            .help(count.map { "Outbox and changes waiting: \($0)" } ?? "Outbox")
            .accessibilityLabel(count.map { "Outbox: \($0) waiting" } ?? "Outbox")
            .meadowPopover(isPresented: $shown) {
                OutboxList(tray: tray, readOnly: composing.digest?.profile == "read-only")
                    .task { await tray.load() }
            }
        }
    }

    /// How many wait, as the button says it: nothing once none do, while the tray stays open with nothing in it.
    static func badge(_ waiting: Int) -> String? {
        waiting > 0 ? "\(waiting)" : nil
    }
}

/// What waits to go out, in the outbox's popover: the changes held for a server, the drafts waiting and the outbox's
/// mail, each with how it stands and what can be done about it, on a popover's paper.
struct OutboxList: View {
    let tray: OutboxTray
    let readOnly: Bool
    @Environment(\.meadow) private var meadow

    var body: some View {
        MeadowFitScroll(maxHeight: 448) {
            VStack(alignment: .leading, spacing: 0) {
                if !tray.held.isEmpty { MeadowPaletteSection(String(localized: "Changes waiting"), pop: true) }
                ForEach(tray.held) { change in
                    let stuck = change.state == "error"
                    OutboxRow(state: stuck ? "stuck" : change.state == "running" ? "trying" : "waiting", failed: stuck,
                              title: "\(OutboxTray.whatItDoes(change)): \(subject(change.subject))",
                              detail: "\(OutboxTray.why(change, readOnly: readOnly)) · \(change.account)") {
                        if stuck { act("Retry") { await tray.act("retry", change.id) } }
                        act("Discard") { await tray.act("discard", change.id) }
                    }
                }
                if !tray.drafts.isEmpty { MeadowPaletteSection(String(localized: "Drafts waiting"), pop: true) }
                ForEach(tray.drafts) { draft in
                    OutboxRow(state: draft.discarding ? "discarded" : "draft", failed: false,
                              title: draft.subject.isEmpty ? "(no subject)" : draft.subject,
                              detail: "\(OutboxTray.why(draft, readOnly: readOnly)) · \(draft.account)") {
                        if !draft.discarding {
                            act("Edit") { await Composing.shared.editDraft(nil, draftId: draft.id) }
                            act("Discard") { await tray.discardDraft(draft.id) }
                        }
                    }
                }
                if !tray.items.isEmpty || (tray.held.isEmpty && tray.drafts.isEmpty) {
                    MeadowPaletteSection(String(localized: "Outbox"), pop: true)
                }
                if tray.items.isEmpty && tray.held.isEmpty && tray.drafts.isEmpty {
                    Text("Nothing queued.")
                        .font(.meadow(.ui, 13))
                        .foregroundStyle(meadow.ink3)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 8)
                }
                ForEach(tray.items) { item in
                    OutboxRow(state: OutboxTray.label(item), failed: item.state == "error", title: item.subject,
                              detail: OutboxTray.detail(item)) {
                        if item.scheduled || item.retrying {
                            act(item.retrying ? "Try Now" : "Send Now") { await tray.sendNow(item.id) }
                            act("Cancel") { await tray.cancel(item.id) }
                        }
                        if item.state == "error" {
                            act(item.unsure ? "Send Again" : "Retry") { await tray.retry(item.id) }
                        }
                    }
                }
            }
        }
        .meadowPop(width: 420)
    }

    private func subject(_ subject: String?) -> String {
        subject.flatMap { $0.isEmpty ? nil : $0 } ?? "(no subject)"
    }

    private func act(_ title: LocalizedStringKey, _ run: @escaping @MainActor () async -> Void) -> some View {
        Button(title) { Task { await run() } }
            .meadowButton(small: true)
    }
}

/// One thing waiting: how it stands on a chip, sun's when it failed, what it is and why it waits, and its buttons.
/// The title keeps to one line, cut short if it must, as the page's `.ob-sub` does; the line under it says the whole of
/// why it waits, wrapping as the page's `.ob-why` does, since its end (when it goes, that it goes only while MailMind is
/// open, what failed) is what a person opened the outbox to read.
struct OutboxRow<Actions: View>: View {
    let state: String
    let failed: Bool
    let title: String
    let detail: String
    @ViewBuilder let actions: () -> Actions
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            MeadowChip(state, tone: failed ? .sun : .plain)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.meadow(.ui, 13.5, .semibold))
                    .foregroundStyle(meadow.ink)
                    .lineLimit(1)
                    .help(title)
                Text(detail)
                    .font(.meadow(.ui, 12))
                    .foregroundStyle(failed ? meadow.rust : meadow.ink3)
            }
            .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 6)
            HStack(spacing: 4) { actions() }.fixedSize()
        }
        .padding(.vertical, 7)
        .padding(.horizontal, 10)
    }
}

/// What a send is doing once its composer has gone, as the window's toasts say it: the seconds with Undo, the time
/// it went out for, or what failed with Edit; and what writing says for a moment.
struct SendFloatView: View {
    private var composing: Composing { .shared }

    var body: some View {
        if let float = composing.float {
            floating(float)
        }
        if let notice = composing.notice {
            MeadowToast(notice)
        }
    }

    @ViewBuilder
    private func floating(_ float: SendFloat) -> some View {
        switch float {
        case .progress(let text, let clock), .done(let text, let clock):
            let said = Self.said(text, clock: clock)
            MeadowToast(icon: said.icon, said.words)
        case .countdown(_, let left, _):
            // The seconds in tabular figures, a little quieter (`.toast .count`), as the mockup counts them.
            let count = Text(verbatim: "\(left)s").monospacedDigit().foregroundStyle(.secondary)
            MeadowToast(icon: .send, text: Text("Sending in \(count)")) {
                MeadowToastButton(title: String(localized: "Undo"), icon: .undo) { composing.undo() }
            }
        case .failed(let text, let detail):
            MeadowToast(icon: .alert, text: Text(text), detail: detail) {
                MeadowToastButton(title: String(localized: "Edit")) { composing.editFailed() }
                MeadowToastButton(title: String(localized: "Dismiss")) { composing.dismissFloat() }
            }
        }
    }

    /// A float's words without the arrow or tick they start with, which the toast's icon says instead.
    static func said(_ text: String, clock: Bool) -> (icon: MeadowIcon, words: String) {
        let words = text.replacingOccurrences(of: #"^[↑✓]\s*"#, with: "", options: .regularExpression)
        if clock { return (.clock, words) }
        return (text.hasPrefix("✓") ? .check : .send, words)
    }
}
#endif
