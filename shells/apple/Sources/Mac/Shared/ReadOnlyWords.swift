#if os(macOS)
import Foundation

/// What the app says of read only (Settings › General's switch, `MAILMIND_PROFILE=read-only`), in the page's words and
/// then where to turn it off, which only the app has a switch for:
/// the status's note while it is on, and what a person is told when the server turned down something that would have
/// reached a mail server (a 409 with `read_only`). The server says no up front, before anything is queued or hidden,
/// so nothing a person does waits for later: what they asked for didn't happen.
enum ReadOnlyWords {
    /// The status's note while read only is on.
    static let note = String(localized: """
        Read only is on: MailMind changes nothing on your mail servers, so sending, filing and starring are turned \
        off. Turn it off in Settings › General.
        """)

    /// What a person is told of something read only turned down.
    static let refused = String(localized: "Read only is on, so nothing was changed. Turn it off in Settings › General.")

    /// Whether `error` is read only's refusal.
    static func refuses(_ error: any Error) -> Bool {
        (error as? Problem)?.isReadOnly == true
    }

    /// `refused` for read only's refusal, else `otherwise`. Read only is asked first, ahead of any other words a 409
    /// has (a send being tried, a draft changed elsewhere, too late to undo).
    static func said(_ error: any Error, else otherwise: @autoclosure () -> String) -> String {
        refuses(error) ? refused : otherwise()
    }
}
#endif
