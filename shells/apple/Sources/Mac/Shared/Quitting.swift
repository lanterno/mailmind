#if os(macOS)
import AppKit

/// Quitting with writing open or mail still to go. Every open composer (the reader's reply and each compose window)
/// saves what it holds as a draft on this Mac first, as an autosave does, so nothing typed since its last autosave is
/// lost; its server's copy follows as an autosave's does, once MailMind is open again.
/// MailMind sends only while it is open, so a message in its Undo window is sent at once rather than left until the
/// next launch, and a message that waits for a later time (scheduled, or to try again) is asked about first: "Quit
/// anyway? It sends when MailMind is next open."
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
        /// Something being written couldn't be saved as a draft.
        case unsaved
        /// A draft's save hadn't answered when the wait ran out: it may yet be kept, or may not.
        case stillSaving
    }

    /// How the open composers' saves stood when quitting stopped waiting for them.
    enum Saving: Equatable, Sendable {
        /// Every one was kept, or there was nothing to save.
        case saved
        /// One said no.
        case refused
        /// One hadn't answered yet.
        case waiting
    }

    /// How long quitting waits for what it sent to go.
    static let wait = Duration.seconds(30)
    /// How long quitting waits for the open composers' drafts to be saved.
    static let saveWait = Duration.seconds(5)

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
        case .unsaved:
            String(localized: """
                A message you’re writing couldn’t be saved as a draft. If you quit now, what you wrote is lost.
                """)
        case .stillSaving:
            String(localized: """
                A message you’re writing is still being saved as a draft. If you quit now, some of what you wrote may \
                be lost.
                """)
        }
        return (String(localized: "Quit anyway?"), text, String(localized: "Quit Anyway"), String(localized: "Don’t Quit"))
    }

    /// Ask MailMind to quit (`terminate`), from wherever the asking is: a button, a menu or a job on the main queue.
    /// A quit that waits (`shouldTerminate`'s `.terminateLater`) settles on the main actor while AppKit waits for its
    /// answer in a run loop of its own. Inside a job of the main queue it never could: the queue runs one job at a
    /// time, so the settling never starts and the app hangs. So the quit is asked from the run loop itself, once the
    /// job asking has ended.
    static func quit(_ terminate: @escaping @MainActor () -> Void = { NSApp.terminate(nil) }) {
        let main = CFRunLoopGetMain()
        CFRunLoopPerformBlock(main, CFRunLoopMode.commonModes.rawValue) {
            MainActor.assumeIsolated { terminate() }
        }
        CFRunLoopWakeUp(main)
    }

    /// The app's answer to being asked to quit: now when nothing is being written and nothing waits to send, else
    /// once it has settled. MailMind asks through `quit`, so its own asking never comes from a job of the main queue.
    static func shouldTerminate(_ app: NSApplication) -> NSApplication.TerminateReply {
        let composing = Composing.shared
        let writing = Array(composing.composers.values)
        // A read-only run sends nothing, now or later; what is being written is still kept on this Mac as a draft.
        let readOnly = composing.digest?.profile == "read-only"
        guard let routes = composing.routes, !(readOnly && writing.isEmpty) else { return .terminateNow }
        Task {
            let quit = await settle(routes, writing: writing, readOnly: readOnly, ask: ask,
                                    say: { composing.say($0, for: 30) })
            app.reply(toApplicationShouldTerminate: quit)
        }
        return .terminateLater
    }

    /// Whether to quit. What each open composer holds is saved as a draft first, waited for up to `saveWait`; one that
    /// couldn't be saved, or hadn't answered yet, is asked about (no means stay, with every composer as it was). Then,
    /// but in a read-only run,
    /// after asking about what waits for later (no means stay, its Undo windows as they were), what is in its Undo
    /// window is sent now and waited for, up to `wait`; what still hadn't gone is asked about too.
    static func settle(_ routes: any Routes, writing: [Composer] = [], readOnly: Bool = false,
                       ask: (Question) async -> Bool, say: (String) -> Void, wait: Duration = Quitting.wait,
                       saveWait: Duration = Quitting.saveWait, poll: Duration = .milliseconds(400)) async -> Bool
    {
        switch await saved(writing, wait: saveWait) {
        case .saved: break
        case .refused: if !(await ask(.unsaved)) { return false }
        case .waiting: if !(await ask(.stillSaving)) { return false }
        }
        guard !readOnly else { return true }
        guard let listed = try? await routes.get(API.Get.Outbox.self, ["limit": "50"]) else { return true }
        let plan = plan(listed.items)
        if plan.later > 0, !(await ask(.later(count: plan.later))) { return false }
        guard !plan.now.isEmpty || !plan.going.isEmpty else { return true }
        say(String(localized: "Sending before MailMind quits…"))
        for id in plan.now {
            _ = try? await routes.post(API.Post.OutboxSendnow.self, API.NamesASend(id: id))
        }
        let watched = Set(plan.now + plan.going)
        let until = AppSeams.clock.instant + wait
        while AppSeams.clock.instant < until {
            try? await AppSeams.clock.sleep(for: poll)
            guard let again = try? await routes.get(API.Get.Outbox.self, ["limit": "50"]) else { break }
            let waiting = again.items.contains { watched.contains($0.id) && ($0.state == "queued" || $0.state == "sending") }
            if !waiting { return true }
        }
        return await ask(.stillGoing)
    }

    /// Save what each composer holds that isn't saved yet, on this Mac, and wait for the saves, up to `wait`: whether
    /// every one was kept, one said no, or one hadn't answered yet. A save that hasn't answered isn't one that failed:
    /// it may yet be kept.
    static func saved(_ writing: [Composer], wait: Duration) async -> Saving {
        let saves = writing.compactMap { $0.saveBeforeQuitting() }
        guard !saves.isEmpty else { return .saved }
        let settled = Settled()
        Task {
            var all = true
            for save in saves {
                // Nil is a composer with nothing to save after all.
                if await save.value == false { all = false }
            }
            settled.saving = all ? .saved : .refused
        }
        let until = AppSeams.clock.instant + wait
        while settled.saving == nil, AppSeams.clock.instant < until {
            try? await AppSeams.clock.sleep(for: .milliseconds(20))
        }
        return settled.saving ?? .waiting
    }

    /// How the saves went, once they have all answered.
    @MainActor
    private final class Settled {
        var saving: Saving?
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
