#if os(macOS)
import AppKit
import SwiftUI

/// How a mail looks in its frame, as the page's `mailbox/mailDoc.ts` and `readPrefs.ts` give it: a stylesheet, and
/// whether a rich mail is inverted to darken it. The body's document comes from the core with no style of its own, so
/// its look is the app's to give. Pure, given the colours.
struct MailLook: Equatable, Sendable {
    /// The reading settings (Aa) a look depends on, each as the server names it.
    struct Prefs: Equatable, Sendable {
        var colors: API.MailColors = .auto
        var font: API.ReadFont = .sans
        var size: API.ReadSize = .m
    }

    /// The colours plain text and a darkened mail take, as CSS colours.
    struct Palette: Equatable, Sendable {
        var ink: String
        var background: String
        var link: String
        var quoteLine: String
        var quoteInk: String
    }

    let css: String
    let inverted: Bool

    /// The reading measure (`WIDTHS` in readPrefs.ts): the widest the reader's column grows.
    static func width(_ width: API.ReadWidth) -> CGFloat {
        switch width {
        case .narrow: 640
        case .medium: 760
        case .wide: 920
        }
    }

    /// The plain-text sizes (`SIZES`).
    static func size(_ size: API.ReadSize) -> Double {
        switch size {
        case .s: 15
        case .m: 16.5
        case .l: 18
        case .xl: 20
        }
    }

    /// System stacks, since a mail frame's policy loads no font (`FONTS`).
    static func font(_ font: API.ReadFont) -> String {
        switch font {
        case .sans: #"-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif"#
        case .serif: #""Iowan Old Style",Charter,"Source Serif 4",Georgia,serif"#
        case .mono: #"ui-monospace,"SF Mono",Menlo,Consolas,monospace"#
        }
    }

    /// The page's plain-text colours in daylight, which the app's light appearance keeps.
    static let light = Palette(ink: "#24221E", background: "#FFFDF8", link: "#2D4B70", quoteLine: "#D5C9B1",
                               quoteInk: "#625B4D")

    private static let quote = "details.mm-quote{margin:10px 0}details.mm-quote>summary{cursor:pointer;list-style:none;"
        + "color:#8a8a8a;font:12px -apple-system,BlinkMacSystemFont,sans-serif;padding:3px 9px;border:1px solid #8a8a8a66;border-radius:6px;display:inline-block}"
        + "details.mm-quote>summary::-webkit-details-marker{display:none}details.mm-quote[open]>summary{margin-bottom:8px}"

    /// Whether the mail is shown dark: the person's choice, else in the dark appearance only plain text is, as rich
    /// mail keeps its own colours.
    static func isDark(html: Bool, prefs: Prefs, darkAppearance: Bool) -> Bool {
        switch prefs.colors {
        case .dark: true
        case .original: false
        case .auto: darkAppearance && !html
        }
    }

    /// The look of a mail, rich (`html`) or plain, in the appearance the window has. `dark` is the palette a dark
    /// mail takes and `light` the one plain text takes otherwise: the app's own.
    static func of(html: Bool, prefs: Prefs, darkAppearance: Bool, dark: Palette, light: Palette = MailLook.light) -> MailLook {
        let darkened = isDark(html: html, prefs: prefs, darkAppearance: darkAppearance)
        // Invert the mail to darken its usually light backgrounds, and invert media back so photos stay true.
        if html && darkened {
            return MailLook(css: "html{background:\(dark.background)}body{margin:14px;overflow-wrap:break-word;"
                + "filter:invert(.92) hue-rotate(180deg)}body img,body picture,body video,body svg,"
                + "body [style*=background-image]{filter:invert(1) hue-rotate(180deg)}"
                + "img{max-width:100%;height:auto;max-height:8000px}table{max-width:100%}\(quote)", inverted: true)
        }
        if html {
            return MailLook(css: "body{font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;"
                + "background:#ffffff;margin:14px;overflow-wrap:break-word}a{color:#0b66c3}"
                + "img{max-width:100%;height:auto;max-height:8000px}blockquote{border-left:3px solid #ccc;"
                + "margin:0 0 0 4px;padding-left:12px;color:#555}table{max-width:100%}\(quote)", inverted: false)
        }
        let colors = darkened ? dark : light
        let line = prefs.font == .mono ? 1.55 : 1.62
        let size = Self.size(prefs.size)
        let px = size == size.rounded() ? String(Int(size)) : String(size)
        return MailLook(css: "body{margin:14px;color:\(colors.ink);background:\(colors.background);"
            + "overflow-wrap:break-word}a{color:\(colors.link)}pre{white-space:pre-wrap;"
            + "font:\(px)px/\(line) \(font(prefs.font));margin:0}blockquote{border-left:3px solid \(colors.quoteLine);"
            + "margin:0 0 0 4px;padding-left:12px;color:\(colors.quoteInk)}\(quote)", inverted: false)
    }

    /// The core's document for a frame with this look in its head. The style is ours, never the mail's, and the
    /// document's policy allows an inline style; the mail's own HTML is in the body, after the head the core wrote.
    func dress(_ document: String) -> String {
        guard let head = document.range(of: "</head>") else { return document }
        var dressed = document
        dressed.insert(contentsOf: "<style>\(css)</style>", at: head.lowerBound)
        return dressed
    }
}

/// A plain mail's quote, folded away below what was written, as the page's `foldPlainQuote` finds it.
enum PlainQuote {
    private static let opener = try! NSRegularExpression(
        pattern: #"^\s*(On\s.+\bwrote:|-{2,}\s*Original Message\s*-{2,}|_{10,}|From:\s)"#)

    private static func opens(_ line: String) -> Bool {
        opener.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil
    }

    private static func quoted(_ line: String) -> Bool {
        line.range(of: #"^\s*>"#, options: .regularExpression) != nil
    }

    private static func blank(_ line: String) -> Bool {
        line.trimmingCharacters(in: .whitespaces).isEmpty
    }

    /// What was written and what it quotes, or nil when there is no quote to fold, or nothing above it.
    static func fold(_ text: String) -> (head: String, quoted: String)? {
        let lines = text.components(separatedBy: "\n")
        var at = lines.firstIndex(where: opens) ?? -1
        if at < 0 {
            var j = lines.count - 1
            while j >= 0 && blank(lines[j]) { j -= 1 }
            if j >= 0 && quoted(lines[j]) {
                while j >= 0 && (quoted(lines[j]) || blank(lines[j])) { j -= 1 }
                at = j + 1
            }
        }
        guard at > 0, !lines[..<at].joined().trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return (lines[..<at].joined(separator: "\n"), lines[at...].joined(separator: "\n"))
    }

    /// The core's document of a plain mail (`frame::of_text`: its text whole and escaped in one `<pre>`) with its
    /// quote folded into a `<details>`, which opens and closes with no script. Any other document is left as it is.
    static func foldDocument(_ document: String) -> String {
        let open = "<body><pre>", close = "</pre></body></html>"
        guard let start = document.range(of: open), document.hasSuffix(close),
              start.upperBound <= document.index(document.endIndex, offsetBy: -close.count) else { return document }
        let inner = String(document[start.upperBound..<document.index(document.endIndex, offsetBy: -close.count)])
        guard let fold = fold(unescaped(inner)) else { return document }
        return String(document[..<start.upperBound]) + escaped(fold.head) + "</pre><details class=\"mm-quote\">"
            + "<summary>··· quoted text</summary><pre>" + escaped(fold.quoted) + close
    }

    /// The core's `frame::escape`, undone: `&amp;` last, so an escaped entity's text stays text.
    static func unescaped(_ text: String) -> String {
        text.replacingOccurrences(of: "&lt;", with: "<").replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "&quot;", with: "\"").replacingOccurrences(of: "&#x27;", with: "'")
            .replacingOccurrences(of: "&amp;", with: "&")
    }

    /// Text as HTML shows it, as the core escapes it.
    static func escaped(_ text: String) -> String {
        text.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;").replacingOccurrences(of: "\"", with: "&quot;")
            .replacingOccurrences(of: "'", with: "&#x27;")
    }
}

/// The colours a mail takes in this appearance: plain text in the system's text colours, so it reads as the reader
/// around it does.
enum ReaderPalette {
    private static func css(_ color: NSColor, in appearance: NSAppearance.Name) -> String {
        var hex = "#000000"
        NSAppearance(named: appearance)?.performAsCurrentDrawingAppearance {
            guard let srgb = color.usingColorSpace(.sRGB) else { return }
            let parts = [srgb.redComponent, srgb.greenComponent, srgb.blueComponent].map { Int(($0 * 255).rounded()) }
            hex = String(format: "#%02X%02X%02X", parts[0], parts[1], parts[2])
            if srgb.alphaComponent < 1 { hex = "rgba(\(parts[0]),\(parts[1]),\(parts[2]),\(String(format: "%.2f", srgb.alphaComponent)))" }
        }
        return hex
    }

    static func of(dark: Bool) -> MailLook.Palette {
        let name: NSAppearance.Name = dark ? .darkAqua : .aqua
        return MailLook.Palette(ink: css(.textColor, in: name), background: css(.textBackgroundColor, in: name),
                                link: css(.linkColor, in: name), quoteLine: css(.separatorColor, in: name),
                                quoteInk: css(.secondaryLabelColor, in: name))
    }

    /// The look of one message, in the window's appearance: plain text in the system's own text colours.
    static func look(html: Bool, prefs: MailLook.Prefs, scheme: ColorScheme) -> MailLook {
        MailLook.of(html: html, prefs: prefs, darkAppearance: scheme == .dark, dark: of(dark: true), light: of(dark: false))
    }
}
#endif
