#if os(macOS)
import AppKit
import SwiftUI
import WebKit

/// A link clicked in a mail body: its href and its text, as the frame's document has them. Both are the mail's, so
/// whatever reads them treats them as data.
struct FrameLink: Equatable, Sendable {
    let href: String
    let text: String
}

/// A mail body in a web view, as the page's MailFrame shows one: the document the route answers, under the policy it
/// carries (a sandbox, no script, nothing from the network but the images a person asked for), given its look and,
/// in a plain mail, its quote folded, as tall as its content so the reader scrolls as one.
///
/// The web view's walls are wave 1's: the mail's own JavaScript off, no navigation away from the document, nothing
/// kept on disk, a scheme that answers only the body's document, and now no link preview and no menu item that opens
/// or downloads anything. Two things the page does by reaching into its frame are done here without any script in the
/// mail's world:
///
/// - The look and the folded quote are written into the document as the scheme answers it: a `<style>` in its head
///   and a `<details>` round the quote, which the document's policy allows and which run nothing.
/// - Link clicks and the content's height are told by a short script of the app's own in an isolated content world
///   (`FrameScript`). WebKit runs the mail's world with JavaScript off, so nothing of the mail runs at all, and the
///   isolated world shares only the document's tree with it: its globals, and the one message handler it posts to,
///   are invisible from the mail's world. The script reads an `<a>`'s href and text, takes the click so the frame goes
///   nowhere, and measures the body; the app treats what it posts as the mail's data, checking a link before anything
///   opens it.
struct MailFrame: NSViewRepresentable {
    let core: Core
    let url: URL
    let look: MailLook
    /// Whether the mail is rich: a plain one's quote is folded.
    let html: Bool
    let title: String
    let open: (FrameLink) -> Void
    /// The content's height in points once it is known, which the reader gives the frame.
    let measured: (CGFloat) -> Void

    func makeCoordinator() -> FrameGuard {
        FrameGuard(core: core, url: url, look: look, html: html)
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.setURLSchemeHandler(context.coordinator, forURLScheme: FrameScheme.scheme)
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.mediaTypesRequiringUserActionForPlayback = .all
        FrameScript.install(in: configuration.userContentController, handler: context.coordinator)
        let view = FrameWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.allowsBackForwardNavigationGestures = false
        view.allowsMagnification = true
        view.allowsLinkPreview = false
        view.setAccessibilityLabel(title)
        // Clear, so a mail that lies on the pane's paper has the paper's grain under it too. Where it can't be, the
        // document paints the paper itself (`MailLook.dress`), set before the load below asks for it.
        context.coordinator.clear = view.clearBackground()
        // Hidden until the script first measures it: the web view's own white never flashes in the dark.
        view.isHidden = true
        context.coordinator.view = view
        context.coordinator.told = { message in
            switch message {
            case .link(let link): open(link)
            case .height(let height): measured(height)
            }
        }
        view.load(URLRequest(url: url))
        context.coordinator.showSoon()
        // Find in Message looks in each frame the reader shows.
        context.coordinator.find = context.environment.readerFind
        context.coordinator.find?.add(view)
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        context.coordinator.told = { message in
            switch message {
            case .link(let link): open(link)
            case .height(let height): measured(height)
            }
        }
    }

    static func dismantleNSView(_ view: WKWebView, coordinator: FrameGuard) {
        view.stopLoading()
        view.navigationDelegate = nil
        FrameScript.remove(from: view.configuration.userContentController)
        coordinator.told = nil
        coordinator.find?.remove(view)
    }
}

/// What the app's script in the frame tells: a link taken, or the content's height.
enum FrameMessage: Equatable {
    case link(FrameLink)
    case height(CGFloat)

    /// A message as the script posts it, or nil for anything else. The text is the mail's, so only what fits is read.
    init?(_ body: Any) {
        guard let fields = body as? [String: Any], let kind = fields["kind"] as? String else { return nil }
        switch kind {
        case "link":
            guard let href = fields["href"] as? String, let text = fields["text"] as? String else { return nil }
            self = .link(FrameLink(href: String(href.prefix(8192)), text: String(text.prefix(2000))))
        case "height":
            guard let value = (fields["value"] as? NSNumber)?.doubleValue, value.isFinite, value >= 0 else { return nil }
            self = .height(CGFloat(min(value, 1_000_000)))
        default:
            return nil
        }
    }
}

/// The app's script in a frame, in a content world of its own: it takes link clicks (a middle click too, as the
/// page's does) and says how tall the content is whenever that changes. The mail's world can't see it or post to it.
@MainActor
enum FrameScript {
    static let world = WKContentWorld.world(name: "mailmind-frame")
    static let handler = "frame"

    /// How tall the mail's own content is whatever height its frame has (the page's `contentHeight`): a mail whose
    /// html and body follow the frame's height measures as tall as the frame, so for that moment the two are held to
    /// their content.
    static let source = """
        (() => {
          const post = (m) => { try { window.webkit.messageHandlers.\(handler).postMessage(m); } catch (e) {} };
          const take = (e) => {
            const t = e.target;
            const at = t && t.nodeType === 1 ? t : t && t.parentElement;
            const a = at && at.closest ? at.closest("a[href]") : null;
            if (!a) return;
            e.preventDefault();
            e.stopPropagation();
            if (e.type === "auxclick" && e.button !== 1) return;
            post({ kind: "link", href: a.getAttribute("href") || "", text: (a.textContent || "").slice(0, 2000) });
          };
          document.addEventListener("click", take, true);
          document.addEventListener("auxclick", take, true);
          const height = () => {
            const root = document.documentElement, body = document.body;
            if (!body) return 0;
            const kept = [root, body].map((e) => [e, e.getAttribute("style")]);
            for (const [e] of kept) {
              e.style.setProperty("height", "auto", "important");
              e.style.setProperty("min-height", "0", "important");
            }
            const needs = body.scrollHeight;
            for (const [e, style] of kept) {
              if (style === null) e.removeAttribute("style"); else e.setAttribute("style", style);
            }
            return needs;
          };
          let asked = false, last = -1;
          const measure = () => {
            if (asked) return;
            asked = true;
            setTimeout(() => {
              asked = false;
              const now = height();
              if (now !== last) { last = now; post({ kind: "height", value: now }); }
            }, 16);
          };
          if (document.body && typeof ResizeObserver !== "undefined") new ResizeObserver(measure).observe(document.body);
          document.addEventListener("toggle", measure, true);
          window.addEventListener("load", measure);
          window.addEventListener("resize", measure);
          measure();
        })();
        """

    static func install(in controller: WKUserContentController, handler messages: any WKScriptMessageHandler) {
        controller.addUserScript(WKUserScript(source: source, injectionTime: .atDocumentEnd, forMainFrameOnly: true,
                                              in: world))
        controller.add(messages, contentWorld: world, name: handler)
    }

    static func remove(from controller: WKUserContentController) {
        controller.removeAllScriptMessageHandlers()
        controller.removeAllUserScripts()
    }
}

/// The frame's web view: the reader scrolls as one, so a wheel that would only move the page up or down goes to the
/// reader's scroll view; its menu keeps only what reads or copies, never what opens or downloads.
final class FrameWebView: WKWebView {
    /// Off once the content is taller than the reader lets a frame grow: then the frame scrolls its own.
    var forwardsScroll = true

    private static let kept: Set<String> = [
        "WKMenuItemIdentifierCopy", "WKMenuItemIdentifierCopyLink", "WKMenuItemIdentifierCopyImage",
        "WKMenuItemIdentifierLookUp", "WKMenuItemIdentifierShareMenu", "WKMenuItemIdentifierSpeechMenu",
        "WKMenuItemIdentifierTranslate", "WKMenuItemIdentifierSearchWeb",
    ]

    /// Turns off the view's own white, and says whether it could. No public API does this on macOS 26
    /// (`underPageBackgroundColor` paints only where a page bounces past its edge), so it is WebKit's private
    /// `_setDrawsBackground:`, reached by key-value coding. That raises for a key it can't find, so the switch is
    /// asked for only where the view answers to it.
    func clearBackground() -> Bool {
        guard responds(to: NSSelectorFromString("_setDrawsBackground:")) else { return false }
        setValue(false, forKey: "drawsBackground")
        return true
    }

    override func scrollWheel(with event: NSEvent) {
        if forwardsScroll, abs(event.scrollingDeltaY) >= abs(event.scrollingDeltaX), let next = nextResponder {
            next.scrollWheel(with: event)
        } else {
            super.scrollWheel(with: event)
        }
    }

    override func willOpenMenu(_ menu: NSMenu, with event: NSEvent) {
        for item in menu.items.reversed() where !item.isSeparatorItem {
            if !Self.kept.contains(item.identifier?.rawValue ?? "") { menu.removeItem(item) }
        }
        // No separator left at either end or twice in a row.
        var previousWasSeparator = true
        for item in menu.items {
            if item.isSeparatorItem && previousWasSeparator { menu.removeItem(item) } else {
                previousWasSeparator = item.isSeparatorItem
            }
        }
        if menu.items.last?.isSeparatorItem == true { menu.removeItem(at: menu.items.count - 1) }
        super.willOpenMenu(menu, with: event)
    }
}

/// Answers the frame's document through the core, dressed in its look, and lets the frame go nowhere else.
@MainActor
final class FrameGuard: NSObject, WKURLSchemeHandler, WKNavigationDelegate, WKScriptMessageHandler {
    private let core: Core
    private let url: URL
    private let look: MailLook
    private let html: Bool
    weak var view: WKWebView?
    var told: ((FrameMessage) -> Void)?
    /// Whether the web view could be made clear: a document dressed for one that couldn't paints its own paper.
    var clear = true
    /// A printed copy's head (`MessagePrinter`): with one, the document starts with it and a plain quote stays open.
    var printHead: String?
    /// Told once the document has loaded, or failed to: a printed copy waits for it.
    var loaded: ((Bool) -> Void)?
    /// The reader's Find, which the frame joined as it was made.
    weak var find: ReaderFind?
    /// The loads still going: one the web view stopped gets nothing more.
    private var going: Set<ObjectIdentifier> = []

    init(core: Core, url: URL, look: MailLook, html: Bool) {
        self.core = core
        self.url = url
        self.look = look
        self.html = html
    }

    /// Show the frame if the script hasn't measured it soon: an empty document says nothing.
    func showSoon() {
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(1.5))
            self?.view?.isHidden = false
        }
    }

    func webView(_ webView: WKWebView, start task: any WKURLSchemeTask) {
        let id = ObjectIdentifier(task)
        going.insert(id)
        guard let asked = task.request.url, let target = FrameScheme.target(of: asked) else {
            going.remove(id)
            task.didFailWithError(URLError(.unsupportedURL))
            return
        }
        let (core, look, html, clear, head) = (core, look, html, clear, printHead)
        Task { @MainActor in
            let answer = await core.send("GET", target, Data())
            guard going.remove(id) != nil else { return }
            var fields: [String: String] = [:]
            for header in answer.headers where header.name.lowercased() != "content-length" {
                fields[header.name] = header.value
            }
            let body = Self.dressed(answer, look: look, html: html, clear: clear, head: head)
            let response = HTTPURLResponse(url: asked, statusCode: Int(answer.status), httpVersion: "HTTP/1.1",
                                           headerFields: fields)
            if let response { task.didReceive(response) }
            task.didReceive(body)
            task.didFinish()
        }
    }

    /// The document with its look, and a plain mail's quote folded. A document that isn't one (a refusal) is left as
    /// the core wrote it. `clear`: whether the frame shows the pane's paper under the document. `head`: a printed
    /// copy's head, put first in the body, with the quote left open, as paper can't unfold it.
    nonisolated static func dressed(_ answer: CoreAnswer, look: MailLook, html: Bool, clear: Bool = true,
                                    head: String? = nil) -> Data
    {
        guard answer.contentType.hasPrefix("text/html"), let document = String(data: answer.body, encoding: .utf8) else {
            return answer.body
        }
        guard let head else {
            return Data(look.dress(html ? document : PlainQuote.foldDocument(document), clear: clear).utf8)
        }
        return Data(MessagePrinter.headed(look.dress(document, clear: clear), head: head).utf8)
    }

    func webView(_ webView: WKWebView, stop task: any WKURLSchemeTask) {
        going.remove(ObjectIdentifier(task))
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loaded?(true)
        loaded = nil
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: any Error) {
        loaded?(false)
        loaded = nil
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: any Error)
    {
        loaded?(false)
        loaded = nil
    }

    /// Only the document asked for loads: a link clicked, a form sent or a redirect goes nowhere.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
        action.request.url == url && action.navigationType == .other ? .allow : .cancel
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        // Only the app's world has this handler; anything else is not ours to hear.
        guard message.world.name == FrameScript.world.name, let read = FrameMessage(message.body) else { return }
        if case .height(let height) = read {
            view?.isHidden = false
            (view as? FrameWebView)?.forwardsScroll = height < MailFrame.tallest
        }
        told?(read)
    }
}
#endif
