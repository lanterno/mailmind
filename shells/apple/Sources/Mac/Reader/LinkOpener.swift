#if os(macOS)
import AppKit
import Foundation

/// What a link clicked in mail does: a mailto writes to that address in MailMind's own composer; a web link opens in
/// the default browser, after a warning when the page's check finds one; anything else opens nowhere. Never inside
/// the frame.
enum LinkOpening: Equatable {
    case compose(to: [String], subject: String)
    case browse(URL, warning: String?)
    case refuse

    /// What the link `href` with `text`, in mail from `senderDomain`, does. Pure.
    static func of(href: String, text: String, senderDomain: String?) -> LinkOpening {
        let trimmed = href.trimmingCharacters(in: .whitespacesAndNewlines)
        // A link in mail writes to its addresses with its subject, as the page's does; its cc and body stay behind.
        if let mail = Mailto.parse(trimmed) { return .compose(to: mail.to, subject: mail.subject) }
        guard trimmed.range(of: "^https?:", options: [.regularExpression, .caseInsensitive]) != nil,
              let url = URL(string: trimmed), url.host() != nil else { return .refuse }
        return .browse(url, warning: LinkRisk.risk(href: trimmed, text: text, senderDomain: senderDomain))
    }
}

/// Does what a link asks, on this Mac: the warning as a sheet on the window, the browser, or the composer.
@MainActor
enum LinkOpener {
    static func open(_ link: FrameLink, senderDomain: String?, in window: NSWindow?) {
        switch LinkOpening.of(href: link.href, text: link.text, senderDomain: senderDomain) {
        case .refuse:
            Composing.shared.say("This link can’t be opened", refused: true)
        case .compose(let to, let subject):
            Composing.shared.newMessage(to: to, subject: subject)
        case .browse(let url, nil):
            AppSeams.links.open(url)
        case .browse(let url, let warning?):
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "Careful — this link points to \(warning)."
            alert.informativeText = "Open \(link.href) anyway?"
            alert.addButton(withTitle: "Cancel")   // the first is the default: Return keeps you here
            alert.addButton(withTitle: "Open Anyway")
            let decide = { (answer: NSApplication.ModalResponse) in
                if answer == .alertSecondButtonReturn { AppSeams.links.open(url) }
            }
            if let window {
                alert.beginSheetModal(for: window, completionHandler: decide)
            } else {
                decide(alert.runModal())
            }
        }
    }
}
#endif
