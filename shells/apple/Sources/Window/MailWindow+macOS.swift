#if os(macOS)
import AppKit
import SwiftUI
import WebKit

/// The Mac app's window: the accounts and their folders, the mail in the one chosen, and the message chosen in it.
struct MailWindow: View {
    @State private var app = MailApp()

    var body: some View {
        content
            .frame(minWidth: 900, minHeight: 560)
            .task { await app.start() }
            .task { await HomeMeasure.shared.watch(app) }
            // Settings, the first run and the status: the toolbar's status, alerts, the welcome and the guide.
            .modifier(StatusHooks(core: app.stage == .open ? app.core : nil))
    }

    @ViewBuilder private var content: some View {
        switch app.stage {
        case .opening:
            ProgressView("Opening MailMind")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(let why):
            ContentUnavailableView("MailMind didn't open", systemImage: "exclamationmark.triangle",
                                   description: Text(why))
        case .open:
            NavigationSplitView {
                // The mailbox's sidebar and list (Mac/Mailbox).
                MailboxSidebar(app: app, mailbox: app.mailbox)
                    .navigationSplitViewColumnWidth(min: 180, ideal: 220)
            } content: {
                MailboxColumn(app: app, mailbox: app.mailbox)
                    .navigationSplitViewColumnWidth(min: 300, ideal: 380)
            } detail: {
                ReaderPane(reader: app.reader)
            }
        }
    }
}

/// The message chosen: who sent it, to whom and when, then its body in its frame.
private struct ReaderPane: View {
    let reader: ReaderModel

    var body: some View {
        switch reader.stage {
        case .empty:
            ContentUnavailableView("No message chosen", systemImage: "envelope")
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(_, let why):
            ContentUnavailableView("This message couldn't be opened", systemImage: "exclamationmark.triangle",
                                   description: Text(why))
        case .open(let copy, let opened):
            VStack(alignment: .leading, spacing: 0) {
                ReaderHeader(opened: opened)
                Divider()
                if let core = reader.core, let url = ReaderModel.frameURL(copy) {
                    MailFrame(core: core, url: url)
                        .id(url)
                }
            }
        }
    }
}

private struct ReaderHeader: View {
    let opened: API.MessageOpened

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(opened.subject.flatMap { $0.isEmpty ? nil : $0 } ?? String(localized: "(no subject)"))
                .font(.title2.weight(.semibold))
                .textSelection(.enabled)
            line("From", from)
            line("To", opened.to)
            if !opened.cc.isEmpty { line("Cc", opened.cc) }
            line("Date", opened.date)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var from: String {
        let address = opened.fromAddr ?? ""
        guard let name = opened.fromName, !name.isEmpty, name != address else { return address }
        return address.isEmpty ? name : "\(name) <\(address)>"
    }

    @ViewBuilder private func line(_ label: LocalizedStringKey, _ value: String) -> some View {
        if !value.isEmpty {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(label).foregroundStyle(.secondary).frame(width: 40, alignment: .trailing)
                Text(value).textSelection(.enabled).lineLimit(2)
            }
            .font(.callout)
        }
    }
}

/// A mail body in a web view, as the page's MailFrame shows one: the document the route answers, under the policy it
/// carries (a sandbox, no script, nothing from the network but the images a person asked for). The web view adds its
/// own walls: JavaScript off, no navigation away from the document, nothing kept on disk, and a scheme that answers
/// only the body's document.
private struct MailFrame: NSViewRepresentable {
    let core: Core
    let url: URL

    func makeCoordinator() -> FrameGuard {
        FrameGuard(core: core, url: url)
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.setURLSchemeHandler(context.coordinator, forURLScheme: FrameScheme.scheme)
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.mediaTypesRequiringUserActionForPlayback = .all
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.allowsBackForwardNavigationGestures = false
        view.allowsMagnification = true
        view.load(URLRequest(url: url))
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {}

    static func dismantleNSView(_ view: WKWebView, coordinator: FrameGuard) {
        view.stopLoading()
        view.navigationDelegate = nil
    }
}

/// Answers the frame's document through the core, and lets the frame go nowhere else.
@MainActor
private final class FrameGuard: NSObject, WKURLSchemeHandler, WKNavigationDelegate {
    private let core: Core
    private let url: URL
    /// The loads still going: one the web view stopped gets nothing more.
    private var going: Set<ObjectIdentifier> = []

    init(core: Core, url: URL) {
        self.core = core
        self.url = url
    }

    func webView(_ webView: WKWebView, start task: any WKURLSchemeTask) {
        let id = ObjectIdentifier(task)
        going.insert(id)
        guard let asked = task.request.url, let target = FrameScheme.target(of: asked) else {
            going.remove(id)
            task.didFailWithError(URLError(.unsupportedURL))
            return
        }
        let core = core
        Task { @MainActor in
            let answer = await core.send("GET", target, Data())
            guard going.remove(id) != nil else { return }
            var fields: [String: String] = [:]
            for header in answer.headers { fields[header.name] = header.value }
            let response = HTTPURLResponse(url: asked, statusCode: Int(answer.status), httpVersion: "HTTP/1.1",
                                           headerFields: fields)
            if let response { task.didReceive(response) }
            task.didReceive(answer.body)
            task.didFinish()
        }
    }

    func webView(_ webView: WKWebView, stop task: any WKURLSchemeTask) {
        going.remove(ObjectIdentifier(task))
    }

    /// Only the document asked for loads: a link clicked, a form sent or a redirect goes nowhere.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
        action.request.url == url && action.navigationType == .other ? .allow : .cancel
    }
}
#endif
