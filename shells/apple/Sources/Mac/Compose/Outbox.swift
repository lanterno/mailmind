#if os(macOS)
import Observation
import SwiftUI

/// The tray the toolbar's outbox opens, as the page's `compose/Outbox.tsx`: changes waiting for a server, drafts
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

    init(routes: any Routes, say: @escaping (String) -> Void) {
        self.routes = routes
        self.say = say
    }

    /// What is going: queued or sending, changes waiting, and drafts waiting.
    var active: Int { items.filter { $0.state == "queued" || $0.state == "sending" }.count + waiting + drafts.count }
    var failed: Int { items.filter { $0.state == "error" }.count + stuck }

    func load() async {
        if let outbox = try? await routes.get(API.Get.Outbox.self, ["limit": "50"]) {
            items = outbox.items
            drafts = outbox.drafts
        }
        if let pending = try? await routes.get(API.Get.Pending.self, [:]) {
            held = pending.items
            waiting = pending.waiting
            stuck = pending.stuck
        }
    }

    // MARK: What each row can do

    private static let ops = ["trash": "Move to Trash", "archive": "Archive", "junk": "Mark as junk", "move": "Move"]
    private static let flags = ["seen": ("Mark unread", "Mark read"), "flagged": ("Remove star", "Star"),
                                "answered": ("Mark unanswered", "Mark answered")]
    private static let why = ["refused": "the server refused it", "no_folder": "no folder to move it to",
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

    static func why(_ change: API.PendingChange, readOnly: Bool) -> String {
        if change.state == "error" { return why[change.error ?? ""] ?? "couldn’t reach the server" }
        if readOnly { return "waits for a full run" }
        return change.attempts > 0 ? "tried \(change.attempts)×, trying again" : "once the server is back"
    }

    static func why(_ draft: API.WaitingDraft, readOnly: Bool) -> String {
        let goes = draft.discarding ? "leaves the server" : "reaches the server"
        if readOnly { return "\(goes) on a full run" }
        return draft.attempts > 0 ? "tried \(draft.attempts)×, trying again" : "\(goes) once it’s back"
    }

    static func label(_ item: API.OutboxItem) -> String {
        item.scheduled ? "scheduled" : item.retrying ? "retrying" : item.unsure ? "unsure" : item.state
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
            say((error as? Problem)?.status == 409 ? "It’s being tried now. Try again in a moment" : "It has already settled")
        }
        await load()
    }

    func discardDraft(_ id: Int) async {
        do {
            _ = try await routes.post(API.Post.Draft.self, API.DraftBody(delete: true, draftId: id))
            say("Draft discarded")
        } catch {
            say("Couldn’t discard it")
        }
        await load()
    }

    func sendNow(_ id: Int) async {
        do {
            _ = try await routes.post(API.Post.OutboxSendnow.self, API.NamesASend(id: id))
            say("Sending now…")
        } catch {
            say("Couldn’t send now")
        }
        await later()
    }

    func cancel(_ id: Int) async {
        do {
            _ = try await routes.post(API.Post.OutboxCancel.self, API.NamesASend(id: id))
            say("Send cancelled")
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
            say("Retry failed")
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

/// The toolbar's outbox: shown while something is going or failed, with how many.
struct OutboxButton: View {
    @State private var tray: OutboxTray?
    @State private var shown = false
    private var composing: Composing { .shared }

    var body: some View {
        Group {
            if let tray, tray.active + tray.failed > 0 || shown {
                Button {
                    shown.toggle()
                } label: {
                    Label("Outbox", systemImage: tray.failed > 0 ? "exclamationmark.arrow.triangle.2.circlepath" : "tray.and.arrow.up")
                }
                .badge(tray.active + tray.failed)
                .help("Outbox and changes waiting: \(tray.active + tray.failed)")
                .popover(isPresented: $shown, arrowEdge: .bottom) {
                    OutboxList(tray: tray, readOnly: composing.digest?.profile == "read-only")
                        .task { await tray.load() }
                }
            }
        }
        .task(id: composing.outboxTurn) {
            if tray == nil, let routes = composing.routes {
                tray = OutboxTray(routes: routes) { composing.say($0) }
            }
            await tray?.load()
        }
    }
}

struct OutboxList: View {
    let tray: OutboxTray
    let readOnly: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                if !tray.held.isEmpty { heading("Changes waiting") }
                ForEach(tray.held) { change in
                    let stuck = change.state == "error"
                    row(state: stuck ? "stuck" : change.state == "running" ? "trying" : "waiting", failed: stuck,
                        title: "\(OutboxTray.whatItDoes(change)): \(change.subject.flatMap { $0.isEmpty ? nil : $0 } ?? "(no subject)")",
                        detail: "\(OutboxTray.why(change, readOnly: readOnly)) · \(change.account)") {
                        if stuck { Button("Retry") { Task { await tray.act("retry", change.id) } } }
                        Button("Discard") { Task { await tray.act("discard", change.id) } }
                    }
                }
                if !tray.drafts.isEmpty { heading("Drafts waiting") }
                ForEach(tray.drafts) { draft in
                    row(state: draft.discarding ? "discarded" : "draft", failed: false,
                        title: draft.subject.isEmpty ? "(no subject)" : draft.subject,
                        detail: "\(OutboxTray.why(draft, readOnly: readOnly)) · \(draft.account)") {
                        if !draft.discarding {
                            Button("Edit") { Task { await Composing.shared.editDraft(nil, draftId: draft.id) } }
                            Button("Discard") { Task { await tray.discardDraft(draft.id) } }
                        }
                    }
                }
                if !tray.items.isEmpty || (tray.held.isEmpty && tray.drafts.isEmpty) { heading("Outbox") }
                if tray.items.isEmpty && tray.held.isEmpty && tray.drafts.isEmpty {
                    Text("Nothing queued.").foregroundStyle(.secondary)
                }
                ForEach(tray.items) { item in
                    let to = item.to.joined(separator: ", ") + (item.bccCount > 0 ? " +\(item.bccCount) bcc" : "")
                    let when = item.scheduled ? " · \(SendTimes.words(item.nextAttemptAt))" : ""
                    row(state: OutboxTray.label(item), failed: item.state == "error", title: item.subject,
                        detail: "to \(to)\(when)\(item.error.map { " · \($0)" } ?? "")") {
                        if item.scheduled || item.retrying {
                            Button(item.retrying ? "Try Now" : "Send Now") { Task { await tray.sendNow(item.id) } }
                            Button("Cancel") { Task { await tray.cancel(item.id) } }
                        }
                        if item.state == "error" {
                            Button(item.unsure ? "Send Again" : "Retry") { Task { await tray.retry(item.id) } }
                        }
                    }
                }
            }
            .padding(14)
        }
        .frame(width: 420)
        .frame(maxHeight: 460)
    }

    private func heading(_ text: String) -> some View {
        Text(text).font(.headline).padding(.top, 4)
    }

    private func row(state: String, failed: Bool, title: String, detail: String,
                     @ViewBuilder actions: () -> some View) -> some View
    {
        HStack(alignment: .top, spacing: 8) {
            Text(state)
                .font(.caption.weight(.semibold))
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background((failed ? Color.red : Color.secondary).opacity(0.15), in: Capsule())
                .foregroundStyle(failed ? .red : .secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).lineLimit(2)
                Text(detail).font(.caption).foregroundStyle(failed ? .red : .secondary).lineLimit(2)
            }
            Spacer(minLength: 6)
            HStack(spacing: 4) { actions() }
                .controlSize(.small)
        }
        .accessibilityElement(children: .combine)
    }
}

/// What a send is doing once its composer has gone, at the foot of the reader: the countdown with Undo, the time
/// it went out for, or what failed with Edit; and what the window says for a moment.
struct SendFloatView: View {
    private var composing: Composing { .shared }

    var body: some View {
        VStack(spacing: 8) {
            if let float = composing.float {
                floating(float)
            }
            if let notice = composing.notice {
                Text(notice)
                    .font(.callout)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .background(.regularMaterial, in: Capsule())
                    .shadow(radius: 3, y: 1)
                    .transition(.opacity)
            }
        }
        .padding(.bottom, 16)
        .animation(.easeOut(duration: 0.15), value: composing.notice)
        .animation(.easeOut(duration: 0.15), value: composing.float)
    }

    private func floating(_ float: SendFloat) -> some View {
        HStack(spacing: 10) {
            switch float {
            case .progress(let text, let clock), .done(let text, let clock):
                if clock { Image(systemName: "clock") }
                Text(text)
            case .countdown(_, let left, _):
                Text("↑ Sending in \(left)s…").monospacedDigit()
                Button("Undo") { composing.undo() }
            case .failed(let text):
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
                Text(text)
                Button("Edit") { composing.editFailed() }
                Button("Dismiss") { composing.dismissFloat() }
            }
        }
        .font(.callout)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.regularMaterial, in: Capsule())
        .shadow(radius: 3, y: 1)
    }
}
#endif
