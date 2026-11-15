import Foundation

/// The screens of the assistant and curation: today's brief, in the reading pane beside the Inbox's list; the answer
/// to a question, in the list's place, so the mail it cites opens beside it; and an account's Screener, across the
/// list's and the pane's room (Meadow's `besideTheInbox` and `spansTheWindow`). `Place.desk` holds one. It is plain
/// data, so the shared `Place` builds on iPhone and iPad too; only the Mac shows them.
enum DeskPlace: Hashable, Sendable {
    /// Today's brief: what needs you, what's on today and next, and who you're waiting on.
    case brief
    /// The answer to the question last asked of your mail, with the mail it cites.
    case ask
    /// One account's Screener: the senders it hasn't decided on.
    case screener(account: String)

    /// What the window's title says there.
    var title: String {
        switch self {
        case .brief: String(localized: "Today’s Brief")
        case .ask: String(localized: "Answer")
        case .screener: String(localized: "Screener")
        }
    }
}
