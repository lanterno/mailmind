#if os(macOS)
import SwiftUI

/// What the reader has open, as a card beside a message reads it: the core, the copy, the message as it opened, its
/// row in the list when it has one, and the conversation when the reader shows one whole.
struct ReaderContext {
    let core: Core
    /// The message the card is about: the open message, or in a conversation its latest (what a reply answers).
    let copy: MailCopy
    /// The message as `/api/message/body` opened it, when it has.
    let opened: API.MessageOpened?
    /// Its row in the list, when the list holds it.
    let row: API.FolderRow?
    /// The conversation shown whole, by its id and account; nil for one message.
    let conversation: (id: String, account: String)?
}

/// The cards other areas show in the reader. Area D's add one line each here, and nothing else in the reader changes:
///
/// - `.top`: under the subject, once for the message or conversation open, where the page's AI card and suggestion
///   card are (`reader/AiCard.tsx`, `SuggestionCard`).
/// - `.message`: in each message, between its bars and its attachments, where the page's invite card with its RSVP is
///   (`Invite` in `reader/Reader.tsx`).
///
/// Each card is its own view taking the context, and shows nothing when it has nothing to say.
struct ReaderCards: View {
    enum Placement {
        case top
        case message
    }

    let placement: Placement
    let context: ReaderContext

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            switch placement {
            case .top:
                EmptyView()
            case .message:
                EmptyView()
            }
        }
    }
}
#endif
