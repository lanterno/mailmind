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
        if trimmed.range(of: "^mailto:", options: [.regularExpression, .caseInsensitive]) != nil {
            let rest = trimmed.dropFirst("mailto:".count)
            let (head, query) = rest.firstIndex(of: "?").map { (rest[..<$0], String(rest[rest.index(after: $0)...])) }
                ?? (rest, "")
            let to = Addresses.parse(String(head).removingPercentEncoding ?? String(head))
            var subject = ""
            for pair in query.split(separator: "&") {
                let parts = pair.split(separator: "=", maxSplits: 1).map(String.init)
                if parts.first?.lowercased() == "subject", parts.count == 2 {
                    subject = parts[1].replacingOccurrences(of: "+", with: " ").removingPercentEncoding ?? parts[1]
                }
            }
            return .compose(to: to, subject: String(subject.prefix(998)))
        }
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
            Composing.shared.say("This link can’t be opened")
        case .compose(let to, let subject):
            Composing.shared.newMessage(to: to, subject: subject)
        case .browse(let url, nil):
            NSWorkspace.shared.open(url)
        case .browse(let url, let warning?):
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "Careful — this link points to \(warning)."
            alert.informativeText = "Open \(link.href) anyway?"
            alert.addButton(withTitle: "Cancel")   // the first is the default: Return keeps you here
            alert.addButton(withTitle: "Open Anyway")
            let decide = { (answer: NSApplication.ModalResponse) in
                if answer == .alertSecondButtonReturn { NSWorkspace.shared.open(url) }
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
