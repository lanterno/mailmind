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

/// Where a frame's document comes from: the core's route, or a test's stand-in for it.
struct FrameSource: Sendable {
    /// The answer to a GET of the frame's route, `target` being its path and query.
    let answer: @Sendable (_ target: String) async -> CoreAnswer

    static func core(_ core: Core) -> FrameSource {
        FrameSource { target in await core.send("GET", target, Data()) }
    }
}

/// A mail body in a web view, as the page's MailFrame shows one: the document the route answers, under the policy it
/// carries (a sandbox, no script, nothing from the network but the images a person asked for), given its look and,
/// in a plain mail, its quote folded, as tall as its content so the reader scrolls as one.
///
/// The web view's walls are wave 1's: the mail's own JavaScript off, no navigation away from the document, nothing
/// kept on disk, a scheme that answers only the body's document, and no link preview and no menu item that opens or
/// downloads anything. The document's policy is a sandbox that doesn't allow scripts, and WebKit holds that for every
/// world in the document, the app's own isolated one too: no event listener or timer runs there, so a script of the
/// app's in the frame would never hear a click or measure again. Instead the app asks, from outside, as the page
/// measures its sandboxed iframe from the parent:
///
/// - The look and the folded quote are written into the document as the scheme answers it: a `<style>` in its head
///   and a `<details>` round the quote, which the document's policy allows and which run nothing.
/// - How tall the content is, and the link under the pointer, under a click or focused when Return is pressed, are
///   short questions (`FrameAsk`) the app's Swift evaluates in an isolated content world of its own, which the mail's
///   world can't see. They ask as the frame loads, as its width changes, after a click or a key in it, and as the
///   pointer moves over it; what they return is the mail's data, read as `FrameMessage` reads it, and a link is
///   checked before anything opens it.
struct MailFrame: NSViewRepresentable {
    let source: FrameSource
    let url: URL
    let look: MailLook
    /// Whether the mail is rich: a plain one's quote is folded.
    let html: Bool
    let title: String
    let open: (FrameLink) -> Void
    /// The content's height in points once it is known, which the reader gives the frame.
    let measured: (CGFloat) -> Void
    /// Where the link the pointer is over goes, as a status line says it; nil once it is over none.
    var hovered: (URL?) -> Void = { _ in }

    func makeCoordinator() -> FrameGuard {
        FrameGuard(source: source, url: url, look: look, html: html)
    }

    func makeNSView(context: Context) -> WKWebView {
        let view = Self.webView(context.coordinator, title: title)
        context.coordinator.told = told
        // Find in Message looks in each frame the reader shows.
        context.coordinator.find = context.environment.readerFind
        context.coordinator.find?.add(view)
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        context.coordinator.told = told
    }

    private func told(_ message: FrameMessage) {
        switch message {
        case .link(let link): open(link)
        case .height(let height): measured(height)
        case .hover(let url): hovered(url)
        }
    }

    /// A frame's web view over `walls`, with the frame's walls, its document asked for: hidden until it is first
    /// measured, so the web view's own white never flashes in the dark.
    static func webView(_ walls: FrameGuard, title: String) -> FrameWebView {
        let configuration = WKWebViewConfiguration()
        configuration.setURLSchemeHandler(walls, forURLScheme: FrameScheme.scheme)
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.mediaTypesRequiringUserActionForPlayback = .all
        let view = FrameWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = walls
        view.allowsBackForwardNavigationGestures = false
        view.allowsMagnification = true
        view.allowsLinkPreview = false
        view.setAccessibilityLabel(title)
        // Clear, so a mail that lies on the pane's paper has the paper's grain under it too. Where it can't be, the
        // document paints the paper itself (`MailLook.dress`), set before the load below asks for it.
        walls.clear = view.clearBackground()
        view.isHidden = true
        view.walls = walls
        walls.view = view
        view.load(URLRequest(url: walls.url))
        walls.showSoon()
        return view
    }

    static func dismantleNSView(_ view: WKWebView, coordinator: FrameGuard) {
        view.stopLoading()
        view.navigationDelegate = nil
        coordinator.stop()
        coordinator.find?.remove(view)
    }
}

/// What the app reads from a frame: a link to open, the content's height, or where the link the pointer is over
/// goes.
enum FrameMessage: Equatable {
    case link(FrameLink)
    case height(CGFloat)
    /// The link under the pointer, as `hover(href:)` reads it: nil for none, or one MailMind wouldn't open.
    case hover(URL?)

    /// What a question to the frame (`FrameAsk`) returned, or nil for anything else. The text is the mail's, so only
    /// what fits is read.
    init?(_ body: Any) {
        guard let fields = body as? [String: Any], let kind = fields["kind"] as? String else { return nil }
        switch kind {
        case "hover":
            // Only the href, as a string, and nothing beside it.
            guard Set(fields.keys) == ["kind", "href"], let href = fields["href"] as? String else { return nil }
            self = .hover(Self.hover(href: href))
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

    /// Where a link the pointer is over goes, to say in the status line: a web address or a mail address, whole and
    /// no longer than a link can be (`LinkOpening`'s schemes); nil for none, an empty href, or one MailMind wouldn't
    /// open (a script's, a file's, a fragment of the mail's own), which the line then leaves unsaid.
    static func hover(href: String) -> URL? {
        let href = href.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !href.isEmpty, href.count <= 8192, !href.unicodeScalars.contains(where: { $0.properties.generalCategory == .control }),
              let url = URL(string: href), let scheme = url.scheme?.lowercased(),
              ["http", "https", "mailto"].contains(scheme) else { return nil }
        if scheme != "mailto", url.host?.isEmpty ?? true { return nil }
        return url
    }
}

/// What the app asks of a frame, each a function body evaluated by Swift in a content world of its own: the mail's
/// world can't see it, and nothing of it stays in the document between questions. The document's sandbox runs no
/// listener or timer in any world, so these are asked from outside and return at once.
@MainActor
enum FrameAsk {
    static let world = WKContentWorld.world(name: "mailmind-frame")

    /// How tall the mail's own content is whatever height its frame has (the page's `contentHeight`): a mail whose
    /// html and body follow the frame's height measures as tall as the frame, so for that moment the two are held to
    /// their content. Nothing until the mail's document is there and read, as the page waits for it: as a load
    /// commits, the web view's first, empty document can still answer, as tall as the frame.
    static let height = """
        if (location.protocol !== "\(FrameScheme.scheme):" || document.readyState === "loading") return null;
        const root = document.documentElement, body = document.body;
        if (!body) return { kind: "height", value: 0 };
        const kept = [root, body].map((e) => [e, e.getAttribute("style")]);
        for (const [e] of kept) {
          e.style.setProperty("height", "auto", "important");
          e.style.setProperty("min-height", "0", "important");
        }
        const needs = body.scrollHeight;
        for (const [e, style] of kept) {
          if (style === null) e.removeAttribute("style"); else e.setAttribute("style", style);
        }
        return { kind: "height", value: needs };
        """

    /// The link at the point `x`, `y` of the frame's viewport, in CSS pixels: its href and its text.
    static let link = """
        const at = document.elementFromPoint(x, y);
        const a = at && at.closest ? at.closest("a[href]") : null;
        if (!a) return null;
        return { kind: "link", href: a.getAttribute("href") || "", text: (a.textContent || "").slice(0, 2000) };
        """

    /// The link focused in the frame, as `link` says one.
    static let focused = """
        const at = document.activeElement;
        const a = at && at.closest ? at.closest("a[href]") : null;
        if (!a) return null;
        return { kind: "link", href: a.getAttribute("href") || "", text: (a.textContent || "").slice(0, 2000) };
        """

    /// The href of the link at the point `x`, `y`, and nothing else: an empty one for none.
    static let hover = """
        const at = document.elementFromPoint(x, y);
        const a = at && at.closest ? at.closest("a[href]") : null;
        return { kind: "hover", href: a ? a.getAttribute("href") || "" : "" };
        """
}

/// The frame's web view: the reader scrolls as one, so a wheel that would only move the page up or down goes to the
/// reader's scroll view; its menu keeps only what reads or copies, never what opens or downloads. What a person does
/// in it (a click, the pointer moving or leaving, a key, a new width) goes to its walls, which ask the document.
final class FrameWebView: WKWebView {
    /// Off once the content is taller than the reader lets a frame grow: then the frame scrolls its own.
    var forwardsScroll = true
    /// What asks the document what a person's click, pointer or key was on.
    weak var walls: FrameGuard?
    /// Where the left button went down, in the viewport: a click opens a link only where it went up there too.
    private var pressed: CGPoint?

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

    /// Where `event` is in the document's viewport, in CSS pixels, as `elementFromPoint` takes it.
    func viewportPoint(of event: NSEvent) -> CGPoint {
        let at = convert(event.locationInWindow, from: nil)
        let scale = max(magnification * pageZoom, 0.01)
        return CGPoint(x: at.x / scale, y: (isFlipped ? at.y : bounds.height - at.y) / scale)
    }

    override func scrollWheel(with event: NSEvent) {
        if forwardsScroll, abs(event.scrollingDeltaY) >= abs(event.scrollingDeltaX), let next = nextResponder {
            next.scrollWheel(with: event)
        } else {
            super.scrollWheel(with: event)
        }
    }

    override func mouseDown(with event: NSEvent) {
        pressed = event.clickCount == 1 && !event.modifierFlags.contains(.control) ? viewportPoint(of: event) : nil
        super.mouseDown(with: event)
    }

    override func mouseUp(with event: NSEvent) {
        super.mouseUp(with: event)
        let at = viewportPoint(of: event)
        // A press that went up where it went down, once, is a click; a drag, a double click or a ⌃-click (the menu)
        // opens nothing.
        let click = pressed.map { hypot(at.x - $0.x, at.y - $0.y) <= 4 } ?? false
        pressed = nil
        walls?.clicked(at: at, opens: click && event.clickCount == 1)
    }

    override func otherMouseUp(with event: NSEvent) {
        super.otherMouseUp(with: event)
        // A middle click opens the link under it, as the page's does.
        walls?.clicked(at: viewportPoint(of: event), opens: event.buttonNumber == 2)
    }

    /// WebKit hears the pointer through tracking areas of its own, which tell WebKit alone, never this view; so the
    /// frame keeps one of its own, its moves and its leaving told to `pointerWatch`.
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard !trackingAreas.contains(where: { $0.owner === pointerWatch }) else { return }
        addTrackingArea(NSTrackingArea(rect: .zero, options: [.mouseMoved, .mouseEnteredAndExited, .activeInKeyWindow,
                                                              .inVisibleRect], owner: pointerWatch, userInfo: nil))
    }

    /// The tracking area's owner, which AppKit doesn't keep: the pointer's moves over the frame and its leaving.
    private lazy var pointerWatch = PointerWatch(frame: self)

    private final class PointerWatch: NSResponder {
        weak var frame: FrameWebView?

        init(frame: FrameWebView) {
            self.frame = frame
            super.init()
        }

        required init?(coder: NSCoder) { nil }

        override func mouseMoved(with event: NSEvent) {
            guard let frame else { return }
            frame.walls?.pointer(at: frame.viewportPoint(of: event))
        }

        override func mouseExited(with event: NSEvent) {
            frame?.walls?.pointerLeft()
        }
    }

    override func keyDown(with event: NSEvent) {
        super.keyDown(with: event)
        // Return or Enter on a focused link opens it, once however long it is held; any key may unfold a quote.
        walls?.keyed(opens: [36, 76].contains(event.keyCode) && !event.isARepeat)
    }

    override func setFrameSize(_ size: NSSize) {
        let widthChanged = size.width != frame.width
        super.setFrameSize(size)
        // A new width wraps the content anew, so it is measured again.
        if widthChanged { walls?.settle(for: .seconds(1)) }
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

/// Answers the frame's document through the core, dressed in its look, lets the frame go nowhere else, and asks the
/// document how tall it is and which link a person meant.
@MainActor
final class FrameGuard: NSObject, WKURLSchemeHandler, WKNavigationDelegate {
    private let source: FrameSource
    let url: URL
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
    /// The height last told.
    private var height: CGFloat?
    /// Measuring again and again while the content may still change, until `settled`.
    private var settling: Task<Void, Never>?
    private var settled = ContinuousClock.now
    /// Where the pointer is to be asked about next, and whether a question about it is going.
    private var pointer: CGPoint?
    private var pointing: Task<Void, Never>?
    /// Counts the pointer's leaving, so an answer about where it was before is dropped.
    private var leftCount = 0
    /// The link last said to be under the pointer.
    private var hovering: URL?

    init(source: FrameSource, url: URL, look: MailLook, html: Bool) {
        self.source = source
        self.url = url
        self.look = look
        self.html = html
    }

    convenience init(core: Core, url: URL, look: MailLook, html: Bool) {
        self.init(source: .core(core), url: url, look: look, html: html)
    }

    /// Show the frame if nothing has measured it soon: an empty document says nothing.
    func showSoon() {
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(1.5))
            self?.view?.isHidden = false
        }
    }

    /// The frame is going: no more questions, and no link said under the pointer.
    func stop() {
        settling?.cancel()
        settling = nil
        pointing?.cancel()
        pointing = nil
        pointer = nil
        leftCount += 1
        told?(.hover(nil))
        told = nil
    }

    // MARK: The document

    func webView(_ webView: WKWebView, start task: any WKURLSchemeTask) {
        let id = ObjectIdentifier(task)
        going.insert(id)
        guard let asked = task.request.url, let target = FrameScheme.target(of: asked) else {
            going.remove(id)
            task.didFailWithError(URLError(.unsupportedURL))
            return
        }
        let (source, look, html, clear, head) = (source, look, html, clear, printHead)
        Task { @MainActor in
            let answer = await source.answer(target)
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

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        // Measured as it is read, so the frame shows before its images have all come. Paper measures itself.
        if printHead == nil { settle(for: .seconds(2)) }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // Its images and faces have come; one that comes late still changes its height for a while.
        if printHead == nil { settle(for: .seconds(5)) }
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

    /// Only the document asked for loads: a link clicked, a form sent or a redirect goes nowhere. A link opens only
    /// as the app asks the document which one a person clicked (`clicked(at:opens:)`).
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
        action.request.url == url && action.navigationType == .other ? .allow : .cancel
    }

    // MARK: Asking the document

    /// Ask the document `question` in the app's world, and read what it returns as the mail's data: nil for nothing,
    /// an error, or anything `FrameMessage` doesn't read.
    private func ask(_ question: String, _ arguments: [String: Any] = [:]) async -> FrameMessage? {
        guard let view else { return nil }
        guard let said = try? await view.callAsyncJavaScript(question, arguments: arguments, in: nil,
                                                             contentWorld: FrameAsk.world) else { return nil }
        return FrameMessage(said)
    }

    private static func place(_ point: CGPoint) -> [String: Any] {
        ["x": Double(point.x), "y": Double(point.y)]
    }

    /// Measure the content, and tell its height when it has changed: the frame shows once it has one, and stops
    /// handing the wheel on once it is taller than the reader lets a frame grow.
    func measure() async {
        guard case .height(let now)? = await ask(FrameAsk.height), now != height, let view else { return }
        height = now
        view.isHidden = false
        (view as? FrameWebView)?.forwardsScroll = now < MailFrame.tallest
        told?(.height(now))
    }

    /// Measure in a moment, then again, more seldom each time, until `span` from now: whatever a load, a new width, a
    /// quote unfolded or an image arriving changes shows within it. Each call starts over, so a splitter dragged
    /// measures once it rests.
    func settle(for span: Duration) {
        let until = ContinuousClock.now + span
        if until > settled { settled = until }
        settling?.cancel()
        settling = Task { @MainActor [weak self] in
            var pause = Duration.milliseconds(16)
            while (try? await Task.sleep(for: pause)) != nil, let walls = self {
                await walls.measure()
                guard ContinuousClock.now < walls.settled else { return }
                pause = min(pause * 2, .seconds(1))
            }
        }
    }

    /// A click, or a middle click, at `point`: the link under it opens when `opens`; anything it unfolded is measured.
    func clicked(at point: CGPoint, opens: Bool) {
        settle(for: .seconds(1))
        guard opens else { return }
        Task { @MainActor [weak self] in
            guard let self, case .link(let link)? = await self.ask(FrameAsk.link, Self.place(point)) else { return }
            self.told?(.link(link))
        }
    }

    /// A key pressed in the frame: Return or Enter opens the link focused (`opens`); anything it unfolded is measured.
    func keyed(opens: Bool) {
        settle(for: .seconds(1))
        guard opens else { return }
        Task { @MainActor [weak self] in
            guard let self, case .link(let link)? = await self.ask(FrameAsk.focused) else { return }
            self.told?(.link(link))
        }
    }

    /// The pointer moved to `point`: the link under it is asked for, one question at a time and no more often than a
    /// frame or two, about where the pointer is by then.
    func pointer(at point: CGPoint) {
        pointer = point
        guard pointing == nil else { return }
        pointing = Task { @MainActor [weak self] in
            while let walls = self, let point = walls.pointer, !Task.isCancelled {
                walls.pointer = nil
                let round = walls.leftCount
                if case .hover(let url)? = await walls.ask(FrameAsk.hover, Self.place(point)), round == walls.leftCount {
                    walls.hover(url)
                }
                try? await Task.sleep(for: .milliseconds(40))
            }
            self?.pointing = nil
        }
    }

    /// The pointer left the frame: no link is under it.
    func pointerLeft() {
        pointer = nil
        leftCount += 1
        hover(nil)
    }

    private func hover(_ url: URL?) {
        guard url != hovering else { return }
        hovering = url
        told?(.hover(url))
    }
}
#endif
