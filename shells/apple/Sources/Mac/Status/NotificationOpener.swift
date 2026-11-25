#if os(macOS)
import AppKit
@preconcurrency import UserNotifications

/// A click on one of MailMind's notifications: the app comes forward, its mail window again if it was closed, and the
/// message the notification named opens there (a new message, or the invite a meeting's reminder came in), as the
/// page's notification opens the invite. A notification carries only the message's name, never its words.
final class NotificationOpener: NSObject, UNUserNotificationCenterDelegate, Sendable {
    static let shared = NotificationOpener()

    /// Hear the clicks, where this process posts for real (`AppSeams.notifications` is `SystemNotifier`): a test or a
    /// measure run posts nothing.
    @MainActor
    static func listen() {
        guard AppSeams.notifications is SystemNotifier else { return }
        UNUserNotificationCenter.current().delegate = shared
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let copy = Self.copy(from: response.notification.request.content.userInfo)
        await MainActor.run { AppStatus.shared.open(fromNotification: copy) }
    }

    /// A message's name as a notification keeps it.
    static func userInfo(_ copy: MailCopy) -> [String: Any] {
        ["account": copy.account, "folder": copy.folder, "key": copy.key, "uid": copy.uid]
    }

    /// The message a notification names, or nil for one that names none.
    static func copy(from info: [AnyHashable: Any]) -> MailCopy? {
        guard let account = info["account"] as? String, let folder = info["folder"] as? String,
              let key = (info["key"] as? NSNumber)?.intValue, let uid = (info["uid"] as? NSNumber)?.intValue,
              !account.isEmpty, !folder.isEmpty else { return nil }
        return MailCopy(key: key, folder: folder, uid: uid, account: account)
    }
}

extension AppStatus {
    /// A notification clicked: the app forward, the mail window opened again if none is open, and the message it
    /// names waiting for the window to open it (`opening`).
    func open(fromNotification copy: MailCopy?) {
        NSApp?.activate()
        if let copy { opening = copy }
        if mailWindows == 0 { reopenWindow?() }
    }
}
#endif
