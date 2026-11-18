#if os(macOS)
import Foundation

/// What the app says of read only (Settings › General's switch, `MAILMIND_PROFILE=read-only`), in the page's words and
/// then, where this run has the switch, where to turn it off:
/// the status's note while it is on, and what a person is told when the server turned down something that would have
/// reached a mail server (a 409 with `read_only`). The server says no up front, before anything is queued or hidden,
/// so nothing a person does waits for later: what they asked for didn't happen.
enum ReadOnlyWords {
    /// The status's note while read only is on.
    static var note: String { note(switchable: switchable) }

    /// What a person is told of something read only turned down.
    static var refused: String { refused(switchable: switchable) }

    static func note(switchable: Bool) -> String {
        let note = String(localized: """
            Read only is on: MailMind changes nothing on your mail servers, so sending, filing and starring are turned \
            off.
            """)
        return switchable ? note + " " + turnOff : note
    }

    static func refused(switchable: Bool) -> String {
        let refused = String(localized: "Read only is on, so nothing was changed.")
        return switchable ? refused + " " + turnOff : refused
    }

    /// Where it is turned off: Settings › General's switch, which only a person's own run has
    /// (`ReadOnlyRun.switchable`). A demo or a test run is read only as it was started, and has no switch to name.
    private static let turnOff = String(localized: "Turn it off in Settings › General.")
    private static let switchable = CoreHome.isAPersonsRun()

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
