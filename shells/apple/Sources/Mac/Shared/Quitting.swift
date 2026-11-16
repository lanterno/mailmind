#if os(macOS)
import AppKit

/// Quitting with mail still to go. MailMind sends only while it is open, so a message in its Undo window is sent at
/// once rather than left until the next launch, and a message that waits for a later time (scheduled, or to try again)
/// is asked about first: "Quit anyway? It sends when MailMind is next open."
@MainActor
enum Quitting {
    /// What the outbox holds as MailMind is asked to quit.
    struct Plan: Equatable, Sendable {
        /// Sends in their Undo window: sent now, before quitting.
        var now: [Int] = []
        /// Sends already on their way, waited for.
        var going: [Int] = []
        /// Sends that wait for a later time: they go when MailMind is next open.
        var later = 0
    }

    /// Why a person is asked before MailMind quits.
    enum Question: Equatable, Sendable {
        /// `count` messages wait for a later time.
        case later(count: Int)
        /// What was sent on quitting hadn't gone when the wait ran out.
        case stillGoing
    }

    /// How long quitting waits for what it sent to go.
    static let wait = Duration.seconds(30)

    static func plan(_ items: [API.OutboxItem]) -> Plan {
        var plan = Plan()
        for item in items {
            switch item.state {
            case "queued" where item.scheduled || item.retrying: plan.later += 1
            case "queued": plan.now.append(item.id)
            case "sending": plan.going.append(item.id)
            default: break
            }
        }
        return plan
    }

    /// The question's words: its title, what it says, and its two buttons, the one that quits first.
    static func words(_ question: Question) -> (title: String, text: String, quit: String, stay: String) {
        let text = switch question {
        case .later(let count) where count == 1:
            String(localized: "A message is waiting to send later. It sends when MailMind is next open.")
        case .later(let count):
            String(localized: "\(count) messages are waiting to send later. They send when MailMind is next open.")
        case .stillGoing:
            String(localized: "A message you sent hasn’t gone yet. It sends when MailMind is next open.")
        }
        return (String(localized: "Quit anyway?"), text, String(localized: "Quit Anyway"), String(localized: "Don’t Quit"))
    }

    /// The app's answer to being asked to quit: now when nothing waits to send, else once it has settled.
    static func shouldTerminate(_ app: NSApplication) -> NSApplication.TerminateReply {
        let composing = Composing.shared
        // A read-only run sends nothing, now or later.
        guard let routes = composing.routes, composing.digest?.profile != "read-only" else { return .terminateNow }
        Task {
            let quit = await settle(routes, ask: ask, say: { composing.say($0, for: 30) })
            app.reply(toApplicationShouldTerminate: quit)
        }
        return .terminateLater
    }

    /// Whether to quit: after asking about what waits for later (no means stay, its Undo windows as they were), what
    /// is in its Undo window is sent now and waited for, up to `wait`; what still hadn't gone is asked about too.
    static func settle(_ routes: any Routes, ask: (Question) async -> Bool, say: (String) -> Void,
                       wait: Duration = Quitting.wait, poll: Duration = .milliseconds(400)) async -> Bool
    {
        guard let listed = try? await routes.get(API.Get.Outbox.self, ["limit": "50"]) else { return true }
        let plan = plan(listed.items)
        if plan.later > 0, !(await ask(.later(count: plan.later))) { return false }
        guard !plan.now.isEmpty || !plan.going.isEmpty else { return true }
        say(String(localized: "Sending before MailMind quits…"))
        for id in plan.now {
            _ = try? await routes.post(API.Post.OutboxSendnow.self, API.NamesASend(id: id))
        }
        let watched = Set(plan.now + plan.going)
        let until = ContinuousClock.now + wait
        while ContinuousClock.now < until {
            try? await Task.sleep(for: poll)
            guard let again = try? await routes.get(API.Get.Outbox.self, ["limit": "50"]) else { break }
            let waiting = again.items.contains { watched.contains($0.id) && ($0.state == "queued" || $0.state == "sending") }
            if !waiting { return true }
        }
        return await ask(.stillGoing)
    }

    /// The question as an alert of the app's own, Quit Anyway first.
    private static func ask(_ question: Question) async -> Bool {
        let said = words(question)
        let alert = NSAlert()
        alert.messageText = said.title
        alert.informativeText = said.text
        alert.addButton(withTitle: said.quit)
        alert.addButton(withTitle: said.stay)
        return alert.runModal() == .alertFirstButtonReturn
    }
}
#endif
