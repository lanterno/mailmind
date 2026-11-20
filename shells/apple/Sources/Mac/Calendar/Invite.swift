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
            said[invite] = .failed(ReadOnlyWords.said(error, else: String(localized: "Couldn’t RSVP: \(DeskRefusal.message(error))")))
            notices.say(ReadOnlyWords.said(error, else: String(localized: "RSVP failed")), refused: true)
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

/// An invite's card in the message, as Meadow's `.invite` draws it (#44): a calendar's leaf with the day, what it
/// is, when and where, who asked and who's coming, the answers, and Add to calendar for the invite's own file.
struct InviteCard: View {
    let copy: MailCopy
    let invite: API.Invite
    /// The part of the message that holds the invite's calendar file, if it has one.
    let part: String?
    let replies: InviteReplies
    let link: CoreLink
    let notices: DeskNotices
    @Environment(\.meadow) private var meadow

    private var answerable: Bool {
        (invite.method == "REQUEST" || invite.method.isEmpty) && !(invite.organizer.email ?? "").isEmpty
    }

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            leaf
            VStack(alignment: .leading, spacing: 0) {
                if invite.method != "REQUEST" && !invite.method.isEmpty
                    || (!invite.status.isEmpty && invite.status != "CONFIRMED") {
                    let status = invite.status.isEmpty || invite.status == "CONFIRMED"
                        ? "" : invite.status.lowercased()
                    Text(verbatim: [invite.method == "REQUEST" ? "" : Self.kind(invite.method), status]
                        .filter { !$0.isEmpty }.joined(separator: " · "))
                        .font(.meadow(.ui, 11, .heavy))
                        .tracking(0.88)
                        .textCase(.uppercase)
                        .foregroundStyle(meadow.rust)
                        .padding(.bottom, 4)
                }
                Text(invite.summary)
                    .font(.meadow(.ui, 16.5, .bold))
                    .foregroundStyle(meadow.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                line(invite.recurrence.isEmpty ? invite.when : "\(invite.when) · \(invite.recurrence)")
                if !invite.location.isEmpty { line(invite.location) }
                if let people = Self.people(organizer: invite.organizer, attendees: invite.attendees) {
                    Text(people)
                        .font(.meadow(.ui, 12.5))
                        .foregroundStyle(meadow.ink3)
                        .lineLimit(2)
                        .padding(.top, 4)
                }
                FlowLayout(spacing: 6) {
                    if answerable { InviteAnswers(copy: copy, replies: replies) }
                    if let part {
                        Button { Task { await addToCalendar(part) } } label: {
                            MeadowIconView(.cal, size: 14)
                            Text("Add to calendar")
                        }
                        .meadowButton(.quiet, small: true)
                        .help("Opens the invite’s calendar file in your calendar app")
                    }
                }
                .padding(.top, 12)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(16)
        .background(meadow.paperRaised, in: RoundedRectangle(cornerRadius: 16))
        .overlay { RoundedRectangle(cornerRadius: 16).strokeBorder(meadow.line, lineWidth: 1) }
        .padding(.top, 16)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(Self.kind(invite.method)): \(invite.summary)")
    }

    /// The calendar's leaf (`.cal`): the month in rust over the day.
    @ViewBuilder private var leaf: some View {
        let day = Self.calendarDay(invite.startIso)
        VStack(spacing: 0) {
            Text(verbatim: day?.month ?? "")
                .font(.meadow(.ui, 11, .heavy))
                .tracking(0.88)
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity, minHeight: 22, maxHeight: 22)
                .background(meadow.rust)
            Text(verbatim: day?.day ?? "")
                .font(.meadow(.display, 27, .bold))
                .foregroundStyle(meadow.ink)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(width: 62, height: 66)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(meadow.line2, lineWidth: 1) }
        .accessibilityHidden(true)
    }

    private func line(_ text: String) -> some View {
        Text(text)
            .font(.meadow(.ui, 13.5))
            .foregroundStyle(meadow.ink2)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
            .padding(.top, 4)
    }

    /// The month, short and in capitals, and the day, of an event that starts at `iso`: the date as the invite
    /// writes it, in the event's own time, so the leaf shows the day the invite names.
    static func calendarDay(_ iso: String, locale: Locale = .current) -> (month: String, day: String)? {
        let parts = iso.prefix(10).split(separator: "-")
        guard parts.count == 3, let month = Int(parts[1]), let day = Int(parts[2]), (1...12).contains(month),
              (1...31).contains(day) else { return nil }
        let formatter = DateFormatter()
        formatter.locale = locale
        let months = formatter.shortStandaloneMonthSymbols ?? formatter.shortMonthSymbols ?? []
        guard months.count == 12 else { return nil }
        return (months[month - 1].replacingOccurrences(of: ".", with: "").uppercased(with: locale), String(day))
    }

    /// Who asked and who's coming, on one line: "From Ada · 3 attendees: Ada, Grace, Alan".
    static func people(organizer: API.Party, attendees: [API.Party]) -> String? {
        let parts = [who(organizer).map { String(localized: "From \($0)") }, Self.attendees(attendees)]
            .compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private func addToCalendar(_ part: String) async {
        guard let routes = link.routes else { return }
        do {
            let file = try await routes.document(API.Get.MessageAttachment.self, [
                "key": String(copy.key), "folder": copy.folder, "uid": String(copy.uid), "part_id": part,
            ])
            try CalendarFile.open(file.data, named: "invite.ics")
        } catch {
            notices.say(DeskRefusal.words(error, else: String(localized: "Couldn’t open the invite’s calendar file.")),
                        refused: true)
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

/// An invite's answer as far as it has gone (`InviteReplies.said`): the answers to give while none is on its way,
/// then sending, sent with Undo while it can be taken back, or taken back with Answer again. The message's card
/// offers each answer and says whom the reply went to; the brief's Today & next, narrower, offers Accept and says
/// only the answer, as the mockup's does. Its parts stand side by side in whatever lays them out.
struct InviteAnswers: View {
    let copy: MailCopy
    let replies: InviteReplies
    var brief = false
    @Environment(\.meadow) private var meadow

    var body: some View {
        switch replies.said[copy] {
        case nil, .failed:
            Button { reply(.accept) } label: {
                if !brief { MeadowIconView(.check, size: 14) }
                Text("Accept")
            }
            .meadowButton(.primary, small: true)
            if !brief {
                Button("Maybe") { reply(.tentative) }
                    .meadowButton(small: true)
                Button("Decline") { reply(.decline) }
                    .meadowButton(small: true)
            }
            if case .failed(let why)? = replies.said[copy] {
                said(why, in: meadow.rust)
            }
        case .sending:
            ProgressView().controlSize(.small)
            said(String(localized: "Sending RSVP…"), in: meadow.ink3)
        case .queued(let queued, _):
            said(words(queued), in: meadow.moss)
            Button("Undo") { Task { await replies.undo(copy) } }
                .buttonStyle(ReaderLinkStyle())
                .focusEffectDisabled()
        case .sent(let queued):
            said(words(queued), in: meadow.moss)
        case .taken:
            said(String(localized: "RSVP not sent"), in: meadow.ink3)
            Button("Answer again") { replies.again(copy) }
                .buttonStyle(ReaderLinkStyle())
                .focusEffectDisabled()
        }
    }

    private func reply(_ decision: API.RsvpDecision) {
        Task { await replies.reply(to: copy, decision) }
    }

    private func said(_ text: String, in colour: Color) -> some View {
        Text(text)
            .font(.meadow(.ui, 13, .semibold))
            .foregroundStyle(colour)
            .frame(minHeight: 28)
    }

    private func words(_ queued: API.RsvpQueued) -> String {
        if brief { return InviteReplies.words(queued.decision) }
        let to = queued.organizer.isEmpty ? String(localized: "organizer") : queued.organizer
        return String(localized: "\(InviteReplies.words(queued.decision)) — reply sent to \(to)")
    }
}
#endif
