#if os(macOS)
import SwiftUI

// A message sent for later (Send later) leaves its draft in Drafts until it goes. Drafts and the reader say so of it,
// in the page's words for a scheduled send ("Scheduled · tomorrow 9:00"), and the reader offers to cancel the send or
// edit the message, which cancels it first: a draft waiting in the outbox can't be changed while it waits.

extension Composing {
    /// The send waiting for later that goes from the draft MailMind keeps as `draftId`, as the outbox names it: a
    /// draft another draft's subject can't be taken for. None for a draft MailMind doesn't keep, which no send goes from.
    func scheduledSend(draftId: Int?) -> API.OutboxItem? {
        guard let draftId else { return nil }
        return scheduled.first { $0.draftId == draftId }
    }

    /// The send waiting for later that goes from a draft of `account`'s with this subject, and to these recipients
    /// when they're known: how a list's row, which names no draft, is known.
    func scheduledSend(account: String, subject: String, to: String? = nil) -> API.OutboxItem? {
        let subject = ScheduledWords.plain(subject)
        let people = to.map(ScheduledWords.people)
        return scheduled.first { item in
            item.account.lowercased() == account.lowercased() && ScheduledWords.plain(item.subject) == subject
                && (people == nil || people == ScheduledWords.people(item.to.joined(separator: ", ")))
        }
    }

    /// What a draft's row says in its time's place, when it waits to go later.
    func scheduledWhen(_ row: API.FolderRow) -> String? {
        scheduledSend(account: row.account, subject: row.subject).map { ScheduledWords.row($0.nextAttemptAt) }
    }

    /// Cancel a send waiting for later: its draft stays in Drafts, to send when the person chooses. True once it's
    /// cancelled.
    @discardableResult
    func cancelScheduled(_ item: API.OutboxItem) async -> Bool {
        guard let core else { return false }
        defer { outboxChanged() }
        do {
            _ = try await core.post(API.Post.OutboxCancel.self, API.NamesASend(id: item.id))
            say(OutboxTray.cancelled(item))
            return true
        } catch {
            say(ReadOnlyWords.said(error, else: (error as? Problem)?.isRefusal == true ? "Too late to cancel"
                                                                                     : "Cancel failed"),
                refused: true)
            return false
        }
    }

    /// Edit a draft waiting to go later: its send is cancelled, then it opens as any draft does.
    func editScheduled(_ item: API.OutboxItem, copy: MailCopy) async {
        guard await cancelScheduled(item) else { return }
        await editDraft(copy)
    }
}

/// How a send waiting for later is said.
enum ScheduledWords {
    /// In a draft's row, in its time's place: the page's words for a scheduled send.
    static func row(_ at: Int) -> String {
        "Scheduled · \(SendTimes.words(at))"
    }

    /// Over a draft in the reader.
    static func line(_ at: Int) -> String {
        String(localized: "Scheduled for \(SendTimes.words(at)) · it goes while MailMind is open")
    }

    /// A subject as the outbox and the list may each say an empty one.
    static func plain(_ subject: String) -> String {
        let trimmed = subject.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed == "(no subject)" ? "" : trimmed
    }

    static func people(_ text: String) -> Set<String> {
        Set(Addresses.parse(text).map { $0.lowercased() })
    }
}

/// Over a draft in the reader that waits to go later, in the trust line's look: when it goes, and Cancel Send and
/// Edit, which cancels the send first.
struct ScheduledLine: View {
    let copy: MailCopy
    let item: API.OutboxItem
    @State private var busy = false
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 8) {
            MeadowIconView(.clock, size: 15)
            Text(ScheduledWords.line(item.nextAttemptAt))
                .truncationMode(.tail)
            Spacer(minLength: 8)
            Button("Cancel Send") { run { await Composing.shared.cancelScheduled(item) } }
                .buttonStyle(ReaderLinkStyle(size: 12))
                .focusEffectDisabled()
                .help("Keep it in Drafts, unsent")
            Button("Edit") { run { await Composing.shared.editScheduled(item, copy: copy) } }
                .buttonStyle(ReaderLinkStyle(size: 12))
                .focusEffectDisabled()
                .help("Cancel the send and open the message to change it")
        }
        .disabled(busy)
        .font(.meadow(.ui, 12.5))
        .foregroundStyle(meadow.ink3)
        .lineLimit(1)
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(meadow.paper2, in: RoundedRectangle(cornerRadius: 10))
        .accessibilityElement(children: .contain)
    }

    private func run(_ action: @escaping @MainActor () async -> Void) {
        busy = true
        Task {
            await action()
            busy = false
        }
    }
}
#endif
