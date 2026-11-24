#if os(macOS)
import SwiftUI
import WebKit

/// The message a reply quotes, or how what you typed will read, in the composer: sanitized HTML in a web view that
/// runs no script, loads nothing from the network and goes nowhere, under the same policy the page's `quoteDoc`
/// gives its frame. Nothing reaches inside it, so no script of the app's runs there either.
struct QuoteFrame: NSViewRepresentable {
    let html: String
    let title: String
    /// Takes the keyboard once it is on screen, so its keys scroll it: the preview, which stands in the text's place.
    var takesKeys = false
    @Environment(\.colorScheme) private var scheme

    private static let policy = "default-src 'none'; script-src 'none'; object-src 'none'; base-uri 'none'; "
        + "form-action 'none'; frame-src 'none'; child-src 'none'; connect-src 'none'; img-src data:; "
        + "style-src 'unsafe-inline'; font-src data:;"

    /// The whole document: the policy, the look a rich mail has in the reader, and the HTML.
    static func document(_ html: String, look: MailLook) -> String {
        "<!doctype html><html lang=\"en\"><head><meta charset=utf-8><base target=\"_blank\">"
            + "<meta http-equiv=\"Content-Security-Policy\" content=\"\(policy)\"><meta name=\"referrer\" content=\"no-referrer\">"
            + "<style>\(look.css)</style></head><body>\(html)</body></html>"
    }

    func makeCoordinator() -> Walls {
        Walls()
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        let view = KeyedWebView(frame: .zero, configuration: configuration)
        view.wantsKeys = takesKeys
        view.navigationDelegate = context.coordinator
        view.allowsLinkPreview = false
        view.setAccessibilityLabel(title)
        load(view, context.coordinator)
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        load(view, context.coordinator)
    }

    /// Load the document, unless it is the one already shown.
    private func load(_ view: WKWebView, _ walls: Walls) {
        let look = ReaderPalette.look(html: true, prefs: MailLook.Prefs(), scheme: scheme)
        let document = Self.document(html, look: look)
        guard walls.loaded != document else { return }
        walls.loaded = document
        view.loadHTMLString(document, baseURL: nil)
    }

    static func dismantleNSView(_ view: WKWebView, coordinator: Walls) {
        view.stopLoading()
        view.navigationDelegate = nil
    }

    /// A web view that takes the keyboard as it comes into its window, when asked to.
    final class KeyedWebView: WKWebView {
        var wantsKeys = false

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard wantsKeys, let window else { return }
            wantsKeys = false
            window.makeFirstResponder(self)
        }
    }

    /// Only the document itself loads: a link clicked goes nowhere.
    @MainActor
    final class Walls: NSObject, WKNavigationDelegate {
        var loaded: String?

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
            action.navigationType == .other && (action.request.url?.absoluteString ?? "") == "about:blank" ? .allow : .cancel
        }
    }
}
#endif
