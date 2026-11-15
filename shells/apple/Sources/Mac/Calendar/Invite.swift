#if os(macOS)
import Observation
import SwiftUI

/// Answers to invites (`/api/invite/rsvp`), as the page's Rsvp sends them: the server builds the reply and queues it
/// with the same window to take it back in as a sent message has, and Undo inside that window cancels it
/// (`/api/outbox/cancel`).
@MainActor
@Observable
final class InviteReplies {
    enum Said: Equatable {
        case sending
        /// Queued: it goes when `goes` comes, unless taken back.
        case queued(API.RsvpQueued, goes: Date)
        case sent(API.RsvpQueued)
        case taken(API.RsvpDecision)
        case failed(String)
    }

    private(set) var said: [MailCopy: Said] = [:]
    @ObservationIgnored let link: CoreLink
    @ObservationIgnored let notices: DeskNotices
    @ObservationIgnored private var going: [MailCopy: Task<Void, Never>] = [:]

    init(link: CoreLink, notices: DeskNotices) {
        self.link = link
        self.notices = notices
    }

    /// The page's words for each answer.
    static func words(_ decision: API.RsvpDecision) -> String {
        switch decision {
        case .accept: String(localized: "Accepted ✓")
        case .tentative: String(localized: "Tentative ?")
        case .decline: String(localized: "Declined ✕")
        }
    }

    func reply(to invite: MailCopy, _ decision: API.RsvpDecision) async {
        guard let routes = link.routes else { return }
        said[invite] = .sending
        do {
            let queued = try await routes.post(API.Post.InviteRsvp.self, API.RsvpBody(
                decision: decision, folder: invite.folder, key: invite.key, uid: invite.uid))
            said[invite] = .queued(queued, goes: Date().addingTimeInterval(TimeInterval(queued.undoSeconds)))
            notices.say(String(localized: "RSVP: \(Self.words(decision))"))
            going[invite]?.cancel()
            going[invite] = Task { [weak self] in
                try? await Task.sleep(for: .seconds(queued.undoSeconds))
                guard !Task.isCancelled, case .queued(let still, _)? = self?.said[invite], still == queued else { return }
                self?.said[invite] = .sent(queued)
            }
        } catch {
            said[invite] = .failed(String(localized: "Couldn’t RSVP: \(DeskRefusal.message(error))"))
            notices.say(String(localized: "RSVP failed"))
        }
    }

    /// Take a queued answer back before it goes.
    func undo(_ invite: MailCopy) async {
        guard let routes = link.routes, case .queued(let queued, _)? = said[invite] else { return }
        going[invite]?.cancel()
        do {
            _ = try await routes.post(API.Post.OutboxCancel.self, API.NamesASend(id: queued.outboxId))
            said[invite] = .taken(queued.decision)
            notices.say(String(localized: "RSVP not sent"))
        } catch {
            said[invite] = .sent(queued)
            let tooLate = (error as? Problem)?.status == 409
            notices.say(tooLate ? String(localized: "✓ Sent — too late to undo") : String(localized: "✓ Sent"))
        }
    }

    /// Let the next answer be chosen again, after one was taken back or failed.
    func again(_ invite: MailCopy) {
        said[invite] = nil
    }
}

/// An invite's card above the mail: what it is, when and where, who asked and who's coming, the answer buttons, and
/// Add to Calendar for the invite's own calendar file.
struct InviteCard: View {
    let copy: MailCopy
    let invite: API.Invite
    /// The part of the message that holds the invite's calendar file, if it has one.
    let part: String?
    let replies: InviteReplies
    let link: CoreLink
    let notices: DeskNotices

    var body: some View {
        DeskCard(tint: .orange) {
            HStack(spacing: 6) {
                Label(Self.kind(invite.method), systemImage: "calendar")
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(.orange)
                if !invite.status.isEmpty && invite.status != "CONFIRMED" {
                    Text(verbatim: "· \(invite.status.lowercased())").font(.callout).foregroundStyle(.secondary)
                }
            }
            Text(invite.summary).font(.title3.weight(.semibold)).textSelection(.enabled)
            Label(invite.recurrence.isEmpty ? invite.when : "\(invite.when) · \(invite.recurrence)", systemImage: "clock")
                .font(.callout)
            if !invite.location.isEmpty {
                Label(invite.location, systemImage: "mappin.and.ellipse").font(.callout).textSelection(.enabled)
            }
            if let organizer = Self.who(invite.organizer) {
                Text("organizer: \(organizer)").font(.caption).foregroundStyle(.secondary)
            }
            if let attendees = Self.attendees(invite.attendees) {
                Text(attendees).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            HStack(spacing: 8) {
                if (invite.method == "REQUEST" || invite.method.isEmpty), !(invite.organizer.email ?? "").isEmpty {
                    answers
                }
                Spacer(minLength: 0)
                if let part {
                    Button("Add to Calendar", systemImage: "calendar.badge.plus") { Task { await addToCalendar(part) } }
                        .help("Opens the invite’s calendar file in your calendar app")
                }
            }
            .controlSize(.small)
        }
    }

    @ViewBuilder private var answers: some View {
        switch replies.said[copy] {
        case nil, .failed:
            HStack(spacing: 6) {
                Button { reply(.accept) } label: { Label("Accept", systemImage: "checkmark") }
                    .tint(.green)
                Button { reply(.tentative) } label: { Label("Maybe", systemImage: "questionmark") }
                Button { reply(.decline) } label: { Label("Decline", systemImage: "xmark") }
                    .tint(.red)
            }
            .buttonStyle(.bordered)
            if case .failed(let why)? = replies.said[copy] {
                Text(why).font(.caption).foregroundStyle(.red)
            }
        case .sending:
            ProgressView().controlSize(.small)
            Text("Sending RSVP…").font(.callout).foregroundStyle(.secondary)
        case .queued(let queued, _):
            Text("\(InviteReplies.words(queued.decision)) — reply sent to \(queued.organizer.isEmpty ? String(localized: "organizer") : queued.organizer)")
                .font(.callout)
                .foregroundStyle(.green)
            Button("Undo") { Task { await replies.undo(copy) } }
                .buttonStyle(.link)
        case .sent(let queued):
            Text("\(InviteReplies.words(queued.decision)) — reply sent to \(queued.organizer.isEmpty ? String(localized: "organizer") : queued.organizer)")
                .font(.callout)
                .foregroundStyle(.green)
        case .taken:
            Text("RSVP not sent").font(.callout).foregroundStyle(.secondary)
            Button("Answer Again") { replies.again(copy) }.buttonStyle(.link)
        }
    }

    private func reply(_ decision: API.RsvpDecision) {
        Task { await replies.reply(to: copy, decision) }
    }

    private func addToCalendar(_ part: String) async {
        guard let routes = link.routes else { return }
        do {
            let file = try await routes.document(API.Get.MessageAttachment.self, [
                "key": String(copy.key), "folder": copy.folder, "uid": String(copy.uid), "part_id": part,
            ])
            try CalendarFile.open(file.data, named: "invite.ics")
        } catch {
            notices.say(DeskRefusal.words(error, else: String(localized: "Couldn’t open the invite’s calendar file.")))
        }
    }

    /// The page's words for what an invite's method makes it.
    static func kind(_ method: String) -> String {
        switch method {
        case "REQUEST": String(localized: "Invitation")
        case "REPLY": String(localized: "RSVP")
        case "CANCEL": String(localized: "Cancelled")
        case "COUNTER": String(localized: "Counter-proposal")
        default: String(localized: "Event")
        }
    }

    static func who(_ party: API.Party) -> String? {
        let name = party.name.flatMap { $0.isEmpty ? nil : $0 }
        return name ?? party.email.flatMap { $0.isEmpty ? nil : $0 }
    }

    /// "3 attendees: Ada, Grace, Alan", the first eight named, then how many more.
    static func attendees(_ people: [API.Party]) -> String? {
        let names = people.prefix(8).compactMap(who)
        guard !names.isEmpty else { return nil }
        let more = people.count > 8 ? " +\(people.count - 8)" : ""
        return countNoun(people.count, "attendee", "attendees") + ": " + names.joined(separator: ", ") + more
    }
}
#endif
