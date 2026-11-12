// A system web view showing one fictional HTML email with JavaScript off, so measure.sh can read the memory of the
// WebKit processes it starts. The email is made here: about 50 KB, a few inline images, every address at example.test.

import AppKit
import ImageIO
import UniformTypeIdentifiers
import WebKit

@main
@MainActor
final class WebViewProbe: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow?
    private var webView: WKWebView?

    static func main() {
        let app = NSApplication.shared
        let probe = WebViewProbe()
        app.delegate = probe
        app.setActivationPolicy(.regular)
        app.run()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences.allowsContentJavaScript = false
        // Nothing is fetched: the email's images are inline, and a mail view never loads remote content unasked.
        config.websiteDataStore = .nonPersistent()
        let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 760, height: 900), configuration: config)
        web.navigationDelegate = self
        let window = NSWindow(contentRect: web.frame, styleMask: [.titled, .closable, .resizable],
                              backing: .buffered, defer: false)
        window.title = "One fictional email"
        window.contentView = web
        window.makeKeyAndOrderFront(nil)
        self.window = window
        self.webView = web
        let html = Self.email()
        print("html_bytes=\(html.utf8.count)")
        web.loadHTMLString(html, baseURL: nil)
    }

    nonisolated func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        Task { @MainActor in
            // Let decoding and painting settle before the footprint is read.
            try? await Task.sleep(for: .seconds(3))
            let content = (webView.value(forKey: "_webProcessIdentifier") as? Int) ?? 0
            print("loaded ui_pid=\(getpid()) webcontent_pid=\(content)")
            fflush(stdout)
            try? await Task.sleep(for: .seconds(8))
            exit(0)
        }
    }

    /// A newsletter-shaped email: a header, paragraphs, a table, and four inline images.
    static func email() -> String {
        let images = (0..<4).map { "data:image/png;base64,\(picture(seed: $0).base64EncodedString())" }
        var body = ""
        for section in 0..<8 {
            body += "<h2>Section \(section + 1): plans for the quarter</h2>"
            for paragraph in 0..<3 {
                body += "<p>Paragraph \(paragraph + 1). The team met on Monday to go over the plan for the next quarter. "
                body += "We agreed to move the review to Thursday, and Ada will send the notes to everyone on the list. "
                body += "Reply to <a href=\"mailto:ada@example.test\">ada@example.test</a> with questions, or read more at "
                body += "<a href=\"https://news.example.test/plan\">news.example.test</a>.</p>"
            }
            if section % 2 == 0 {
                body += "<p><img src=\"\(images[section / 2])\" width=\"600\" height=\"180\" alt=\"Figure \(section / 2 + 1)\"></p>"
            }
            body += "<table border=\"1\" cellpadding=\"6\"><tr><th>Item</th><th>Owner</th><th>Due</th></tr>"
            for row in 0..<4 {
                body += "<tr><td>Task \(section).\(row)</td><td>grace@example.test</td><td>Friday</td></tr>"
            }
            body += "</table>"
        }
        return """
        <!doctype html><html><head><meta charset="utf-8"><title>Weekly update</title>
        <style>body{font:15px -apple-system,sans-serif;max-width:680px;margin:24px auto;color:#222}
        h2{font-size:18px;margin-top:28px}table{border-collapse:collapse;margin:12px 0}img{max-width:100%}</style>
        </head><body><p>From: Ada Example &lt;ada@example.test&gt;<br>To: team@example.test</p>\(body)
        <p style="color:#888">You get this because you are on team@example.test.</p></body></html>
        """
    }

    /// A 600 by 180 picture whose PNG is a few kilobytes, the same for each seed.
    static func picture(seed: Int) -> Data {
        let width = 600, height = 180
        let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                space: CGColorSpaceCreateDeviceRGB(),
                                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.setFillColor(CGColor(red: 0.85, green: 0.9, blue: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        var state = UInt32(seed + 1) &* 2_654_435_761
        for _ in 0..<8 {
            state = state &* 1_664_525 &+ 1_013_904_223
            let x = CGFloat(state % 600), y = CGFloat((state >> 10) % 180), r = CGFloat(10 + (state >> 20) % 40)
            context.setFillColor(CGColor(red: CGFloat(state % 255) / 255, green: 0.4, blue: 0.7, alpha: 0.6))
            context.fillEllipse(in: CGRect(x: x, y: y, width: r, height: r))
        }
        let data = NSMutableData()
        let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil)!
        CGImageDestinationAddImage(destination, context.makeImage()!, nil)
        CGImageDestinationFinalize(destination)
        return data as Data
    }
}
