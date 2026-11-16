#if os(macOS)
import AppKit
import Foundation

/// A mailto: link's message (RFC 6068): its addresses, and the cc, subject and body it asks for. A link from another
/// app or a web page may ask for anything, so a blind copy is never taken from one, and what it asks only fills in a
/// composer: a person still reads it and sends it.
struct Mailto: Equatable {
    var to: [String] = []
    var cc: [String] = []
    var subject = ""
    var body = ""

    /// A subject is one header line (RFC 5322's 998), and a body a link brings is kept to a page's worth.
    static let subjectLimit = 998
    static let bodyLimit = 20_000

    /// The message `href` asks for, or nil when it isn't a mailto: link.
    static func parse(_ href: String) -> Mailto? {
        let trimmed = href.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.range(of: "^mailto:", options: [.regularExpression, .caseInsensitive]) != nil else { return nil }
        let rest = trimmed.dropFirst("mailto:".count)
        let (head, query) = rest.firstIndex(of: "?").map { (rest[..<$0], String(rest[rest.index(after: $0)...])) }
            ?? (rest, "")
        var mail = Mailto(to: Addresses.parse(decoded(String(head), plusIsSpace: false)))
        for pair in query.split(separator: "&") {
            let parts = pair.split(separator: "=", maxSplits: 1).map(String.init)
            guard parts.count == 2 else { continue }
            switch parts[0].lowercased() {
            case "to": mail.to += Addresses.parse(decoded(parts[1], plusIsSpace: false))
            case "cc": mail.cc += Addresses.parse(decoded(parts[1], plusIsSpace: false))
            case "subject": mail.subject = String(decoded(parts[1]).prefix(subjectLimit))
            case "body": mail.body = String(decoded(parts[1]).prefix(bodyLimit))
            default: break
            }
        }
        // One line for the subject: a link can't start the headers' next line.
        mail.subject = mail.subject.components(separatedBy: .newlines).joined(separator: " ")
        return mail
    }

    /// A value percent-decoded. In words `+` is a space, as the page reads a subject; in an address it is itself
    /// (ada+news@example.test).
    private static func decoded(_ value: String, plusIsSpace: Bool = true) -> String {
        let spaced = plusIsSpace ? value.replacingOccurrences(of: "+", with: " ") : value
        return spaced.removingPercentEncoding ?? spaced
    }

    /// The composer's start for it.
    var seed: ComposeSeed {
        var seed = ComposeSeed()
        seed.to = to
        seed.cc = cc
        seed.subject = subject
        if !body.isEmpty { seed.body = body }
        return seed
    }
}

/// The app's delegate, for the mailto: links macOS hands MailMind (when it is the mail app, or a link names it): each
/// opens the composer, once the window can write. Handling them here keeps SwiftUI from opening a window for each.
/// And for quitting, which waits for mail in its Undo window to go (Mac/Shared/Quitting.swift).
@MainActor
final class MailMindDelegate: NSObject, NSApplicationDelegate {
    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls {
            if let mail = Mailto.parse(url.absoluteString) { Composing.shared.write(mail) }
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        Quitting.shouldTerminate(sender)
    }
}
#endif
