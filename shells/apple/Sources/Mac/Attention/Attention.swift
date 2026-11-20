#if os(macOS)
import Foundation
import Observation

/// When a snoozed message comes back: one of the server's presets, or a time of a person's own.
enum Snoozing: Hashable, Sendable {
    case preset(API.SnoozePreset)
    case until(Date)

    /// The page's words for each preset, in the menu's order, with the digit that picks it there: all in the page's
    /// sentence case (`SnoozeMenu.tsx`), so no preset reads as a title beside another that doesn't.
    static func words(_ preset: API.SnoozePreset) -> String {
        switch preset {
        case .laterToday: String(localized: "Later today")
        case .tomorrow: String(localized: "Tomorrow 8am")
        case .weekend: String(localized: "This weekend")
        case .nextWeek: String(localized: "Next week")
        }
    }
}

/// The places attention keeps mail in, by the names of their lists (`/api/messages` reads each as a folder): what a
/// verb on a message there does differs, as the page's reader decides by `m.folder`.
enum AttentionStack: String, Hashable, Sendable, CaseIterable {
    case snoozed = "__snoozed__"
    case muted = "__muted__"
    case waiting = "__waiting__"
    case replyLater = "__replylater__"

    /// The stack a list's folder name is, if it is one.
    init?(folder: String?) {
        guard let folder, let stack = Self(rawValue: folder) else { return nil }
        self = stack
    }
}

/// What a person asks of attention for the messages chosen: snooze and its undoing, mute, Reply Later, a follow-up
/// dismissed. Each goes to the route the page sends it to and says how it went in the page's words; what can be taken
/// back offers Undo. The lists are read again after each, as the page's `readAgain` does.
@MainActor
@Observable
final class AttentionModel {
    /// The verb running, so a second press waits for the first.
    private(set) var acting = false
    /// The messages a time of a person's own is being asked for, while the window's dialog asks it
    /// (`SnoozeTimeDialog`).
    var choosingTime: [MailCopy]?
    @ObservationIgnored let link: CoreLink
    @ObservationIgnored let notices: DeskNotices
    @ObservationIgnored var changed: () -> Void = {}
    /// What the window does once a verb takes mail off the place it shows (a snooze, a mute, Reply Later's Done),
    /// worked out before the verb is sent, while the list still holds it, and run once the server has taken it: the
    /// desk's moves on as File does (`Mailbox.leaving`).
    @ObservationIgnored var leaving: @MainActor ([MailCopy]) -> @MainActor () -> Void = { _ in {} }
    /// The subject of the one message a verb acts on when it isn't the one on screen, for words that say which
    /// (`Mailbox.subjectAway`); nil when it is, or for several.
    @ObservationIgnored var naming: @MainActor ([MailCopy]) -> String? = { _ in nil }

    init(link: CoreLink, notices: DeskNotices) {
        self.link = link
        self.notices = notices
    }

    /// Snooze `copies` until `when`: one message as one, several as one request naming each, under one undo group.
    func snooze(_ copies: [MailCopy], _ when: Snoozing) async {
        var body = API.SnoozeBody()
        switch when {
        case .preset(let preset): body.preset = preset
        case .until(let date): body.until = .int(Int(date.timeIntervalSince1970.rounded(.up)))
        }
        if let one = copies.first, copies.count == 1 {
            (body.folder, body.key, body.uid) = (one.folder, one.key, one.uid)
        } else {
            body.copies = copies.map(\.aCopy)
            body.undoGroup = newUndoGroup()
        }
        // Snoozed, it leaves the list until it wakes, and the words say until when, and which (`SnoozeWords`).
        let subject = naming(copies)
        await act(copies, left: leaving(copies),
                  fail: { Self.couldnt(String(localized: "Couldn’t snooze"), $0) }) { routes in
            let snoozed = try await routes.post(API.Post.Snooze.self, body)
            return (SnoozeWords.said(until: snoozed.until, count: copies.count, subject: subject),
                    String(localized: "Snooze"))
        }
    }

    /// Brought back now: off the Snoozed stack, the one place it is offered, so it leaves the list shown.
    func unsnooze(_ copies: [MailCopy]) async {
        let body = copies.count == 1
            ? API.NamesThreadOrMessages(folder: copies[0].folder, key: copies[0].key, uid: copies[0].uid)
            : API.NamesThreadOrMessages(copies: copies.map(\.aCopy))
        await act(copies, left: leaving(copies), fail: { _ in String(localized: "Couldn’t unsnooze") }) { routes in
            _ = try await routes.post(API.Post.Unsnooze.self, body)
            return (String(localized: "Unsnoozed"), nil)
        }
    }

    /// Mute the thread of each copy. Several go one request each under one undo group, as the page's bulk mute does.
    /// Read only turns every one down, so its no ends the rest unasked and is said as itself. Muted, a thread leaves
    /// the list shown, so the reader moves on as the page's mute does.
    func mute(_ copies: [MailCopy]) async {
        if copies.count == 1 {
            await act(copies, left: leaving(copies),
                      fail: { Self.couldnt(String(localized: "Couldn’t mute"), $0) }) { routes in
                _ = try await routes.post(API.Post.Mute.self, API.MuteBody(folder: copies[0].folder, key: copies[0].key,
                                                                           uid: copies[0].uid))
                return (String(localized: "Muted"), String(localized: "Mute"))
            }
            return
        }
        let group = newUndoGroup()
        await act(copies, left: leaving(copies), fail: { _ in String(localized: "Couldn’t mute") }) { routes in
            var done = 0
            for copy in copies {
                do {
                    _ = try await routes.post(API.Post.Mute.self, API.MuteBody(
                        folder: copy.folder, key: copy.key, uid: copy.uid, undoGroup: group))
                    done += 1
                } catch let refusal as Problem where refusal.isReadOnly {
                    throw refusal
                } catch {}
            }
            return ("Muted " + countNoun(done, "thread", "threads"), done > 0 ? String(localized: "Mute") : nil)
        }
    }

    /// Off the Muted stack, the one place it is offered, so it leaves the list shown.
    func unmute(_ copies: [MailCopy]) async {
        await each(copies, said: String(localized: "Unmuted"), left: leaving(copies),
                   fail: { _ in String(localized: "Couldn’t unmute") }) {
            _ = try await $0.post(API.Post.Unmute.self, API.NamesAThread(folder: $1.folder, key: $1.key, uid: $1.uid))
        }
    }

    /// Reply Later pins the thread in a stack of its own, and doesn't take it off the Inbox as snooze does.
    func replyLater(_ copies: [MailCopy]) async {
        await each(copies, said: String(localized: "Saved to Reply later"),
                   fail: { Self.couldnt(String(localized: "Couldn’t save"), $0) }) {
            _ = try await $0.post(API.Post.Replylater.self,
                                  API.ReplyLaterBody(folder: $1.folder, key: $1.key, on: true, uid: $1.uid))
        }
    }

    /// Done with it: off the Reply Later stack, the one place it is offered, so it leaves the list shown.
    func replyLaterDone(_ copies: [MailCopy]) async {
        await each(copies, said: String(localized: "Done"), left: leaving(copies),
                   fail: { _ in String(localized: "Couldn’t update") }) {
            _ = try await $0.post(API.Post.Replylater.self,
                                  API.ReplyLaterBody(folder: $1.folder, key: $1.key, on: false, uid: $1.uid))
        }
    }

    /// No longer waiting on a reply to it: off the Waiting stack, the one place it is offered, so it leaves the list
    /// shown.
    func dismissFollowup(_ copies: [MailCopy]) async {
        await each(copies, said: String(localized: "Dismissed"), left: leaving(copies),
                   fail: { _ in String(localized: "Couldn’t dismiss") }) {
            _ = try await $0.post(API.Post.FollowupsDismiss.self,
                                  API.NamesAThread(folder: $1.folder, key: $1.key, uid: $1.uid))
        }
    }

    // MARK: Asking

    /// One request for each copy, in turn.
    private func each(_ copies: [MailCopy], said: String, left: (@MainActor () -> Void)? = nil,
                      fail: (String) -> String, _ send: (any RouteCaller, MailCopy) async throws -> Void) async {
        await act(copies, left: left, fail: fail) { routes in
            for copy in copies {
                try await send(routes, copy)
            }
            return (said, nil)
        }
    }

    /// Ask, then say how it went: the words and what Undo takes back, if it can (Edit ▸ Undo's name for it), or the
    /// failure in the page's words. Once done, the window goes where `left` says, as mail that left the place it shows.
    private func act(_ copies: [MailCopy], left: (@MainActor () -> Void)? = nil, fail: (String) -> String,
                     _ call: (any RouteCaller) async throws -> (String, String?)) async {
        guard let routes = link.routes, !copies.isEmpty, !acting else { return }
        acting = true
        defer { acting = false }
        do {
            let (said, undo) = try await call(routes)
            notices.say(said, undo: undo)
            left?()
        } catch {
            notices.say(ReadOnlyWords.said(error, else: fail(DeskRefusal.message(error))))
        }
        changed()
    }

    /// "Couldn't snooze: why", or only "Couldn't snooze" when there are no words for why.
    static func couldnt(_ what: String, _ why: String) -> String {
        why.isEmpty ? what : "\(what): \(why)"
    }
}
#endif
