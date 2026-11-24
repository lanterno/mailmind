#if os(macOS)
import AppKit
import SwiftUI
import WebKit

/// File › Print (⌘P) for the open message: its subject, who it's from and to, and when, over its body, on paper.
///
/// The body is the frame's own document, asked of the core again in a web view of its own off the window, so a
/// conversation, a body taller than the reader or a window in Night all print the same: in the Day light, with every
/// fold open, the quote's and the mail's own. It has the frame's walls: the mail's JavaScript off, nothing kept on disk, the frame's scheme the only
/// one answered, and no navigation. Remote images stay held back, as the frame holds them, unless a person asked to
/// see this message's (`ShownImages`): then they print, as a ticket's code must.
@MainActor
final class MessagePrinter: NSObject {
    static let shared = MessagePrinter()

    /// The copy being printed, its web view and guard, kept until the print panel is done with them.
    private var going: (view: WKWebView, guard: FrameGuard)?

    /// Load the message's document with its head, then show the print panel over `window`.
    func print(_ copy: MailCopy, opened: API.MessageOpened, core: Core, prefs: ReadingPrefs, window: NSWindow?,
               shown: ShownImages = .shared) {
        guard going == nil, let url = Self.documentURL(copy, shown: shown) else { return }
        let guarded = FrameGuard(core: core, url: url, look: Self.look(html: opened.hasHtml, prefs: prefs),
                                 html: opened.hasHtml)
        guarded.printHead = Self.head(opened)
        let configuration = WKWebViewConfiguration()
        configuration.setURLSchemeHandler(guarded, forURLScheme: FrameScheme.scheme)
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.mediaTypesRequiringUserActionForPlayback = .all
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 640, height: 900), configuration: configuration)
        view.navigationDelegate = guarded
        guarded.view = view
        going = (view, guarded)
        let title = opened.subject
        guarded.loaded = { [weak self, weak window] ok in
            guard let self else { return }
            guard ok else {
                self.going = nil
                return
            }
            let operation = Self.operation(view, title: title)
            if let window {
                operation.runModal(for: window, delegate: self,
                                   didRun: #selector(Self.printed(_:success:contextInfo:)), contextInfo: nil)
            } else {
                operation.run()
                self.going = nil
            }
        }
        view.load(URLRequest(url: url))
    }

    /// The document a copy prints from: the frame's, with the remote images a person asked to see.
    static func documentURL(_ copy: MailCopy, shown: ShownImages) -> URL? {
        ReaderModel.frameURL(copy, images: shown.contains(copy))
    }

    @objc private func printed(_ operation: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
        going = nil
    }

    /// The print operation for a loaded copy, the subject as the job's name. WebKit lays the document out for the
    /// paper and splits it into pages itself, but only once the view it prints from has a size.
    static func operation(_ view: WKWebView, title: String?) -> NSPrintOperation {
        let info = (NSPrintInfo.shared.copy() as? NSPrintInfo) ?? NSPrintInfo()
        let operation = view.printOperation(with: info)
        operation.jobTitle = Self.jobTitle(title)
        operation.showsPrintPanel = true
        operation.showsProgressPanel = true
        operation.view?.frame = view.bounds
        return operation
    }

    /// The job's name: the subject, or a plain name for none.
    nonisolated static func jobTitle(_ subject: String?) -> String {
        let subject = (subject ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return subject.isEmpty ? String(localized: "Message") : subject
    }

    /// The look on paper: the Day light's prose and the mail's own colours, never darkened, and every `<details>` open
    /// (`unfolded`).
    static func look(html: Bool, prefs: ReadingPrefs) -> MailLook {
        var set = prefs.look
        set.colors = .original
        let look = MailLook.of(html: html, prefs: set, darkAppearance: false, dark: ReaderPalette.of(dark: true))
        return MailLook(css: look.css + unfolded, inverted: look.inverted, sheet: look.sheet,
                        readingFace: look.readingFace, paper: look.paper)
    }

    /// Paper can't unfold anything, so every `<details>` prints open, the core's quote and the mail's own, through
    /// the part of it that holds what it folds (`::details-content`). The quote's pill, which only opens it, is left
    /// off, and the mail's own summaries show the open marker (▼), as an open fold's do, not the closed one (▶). A
    /// WebKit that doesn't know `::details-content` (Safari 18.4's brought it, macOS 15.4's) drops every rule and
    /// prints the folds closed, as before, under the closed marker.
    static let unfolded = "details::details-content{content-visibility:visible}"
        + "@supports selector(::details-content){details.mm-quote>summary{display:none}"
        + "details>summary:first-of-type{list-style-type:disclosure-open}}"

    /// The head a printed copy starts with: the subject, then From, To, Cc and Date, each the mail's own words
    /// escaped, so nothing in them is read as markup.
    nonisolated static func head(_ opened: API.MessageOpened) -> String {
        let from: String = {
            let name = (opened.fromName ?? "").trimmingCharacters(in: .whitespaces)
            let address = opened.fromAddr ?? ""
            if name.isEmpty || name == address { return address }
            return address.isEmpty ? name : "\(name) <\(address)>"
        }()
        let lines: [(String, String)] = [
            (String(localized: "From"), from), (String(localized: "To"), opened.to), (String(localized: "Cc"), opened.cc),
            (String(localized: "Date"), opened.date),
        ].filter { !$0.1.trimmingCharacters(in: .whitespaces).isEmpty }
        let line = "margin:2px 0;font:13px/1.4 -apple-system,BlinkMacSystemFont,sans-serif;color:#24221E"
        let subject = (opened.subject ?? "").isEmpty ? String(localized: "(no subject)") : opened.subject ?? ""
        var out = "<div style=\"margin:0 0 18px;padding:0 0 12px;border-bottom:1px solid #D5C9B1\">"
        out += "<div style=\"margin:0 0 8px;font:700 18px/1.3 -apple-system,BlinkMacSystemFont,sans-serif;"
            + "color:#24221E\">\(PlainQuote.escaped(subject))</div>"
        for (label, value) in lines {
            out += "<div style=\"\(line)\"><b>\(PlainQuote.escaped(label)):</b> \(PlainQuote.escaped(value))</div>"
        }
        return out + "</div>"
    }

    /// A dressed document with `head` first in its body: after the body's opening tag, or, with none, after the head.
    nonisolated static func headed(_ document: String, head: String) -> String {
        var out = document
        if let body = out.range(of: "<body", options: .caseInsensitive),
           let close = out.range(of: ">", range: body.upperBound..<out.endIndex)
        {
            out.insert(contentsOf: head, at: close.upperBound)
        } else if let end = out.range(of: "</head>", options: .caseInsensitive) {
            out.insert(contentsOf: head, at: end.upperBound)
        } else {
            out = head + out
        }
        return out
    }
}
#endif
