#if os(macOS)
import AppKit
import SwiftUI

/// How a mail looks in its frame, as Meadow's `.prose` sets it on the pane's paper (#44), from the reading settings
/// the page's `readPrefs.ts` keeps: a stylesheet, and whether a rich mail is inverted to darken it. The body's
/// document comes from the core with no style of its own, so its look is the app's to give. Pure, given the colours.
struct MailLook: Equatable, Sendable {
    /// The reading settings (Aa) a look depends on, each as the server names it.
    struct Prefs: Equatable, Sendable {
        var colors: API.MailColors = .auto
        var font: API.ReadFont = .sans
        var size: API.ReadSize = .m
    }

    /// The colours a mail takes, as CSS colours. The background is a sheet's: where the mail is as dark as the light,
    /// the frame is clear, and the pane's own paper, grain and all, shows through.
    struct Palette: Equatable, Sendable {
        var ink: String
        var background: String
        var link: String
        var quoteLine: String
        var quoteInk: String
        /// The quoted text's fold: a pill on raised paper.
        var raised = "#FFFDF8"
    }

    let css: String
    let inverted: Bool
    /// A rich mail in its own light colours at night: a sheet of its own on the night's paper, which the reader rounds.
    var sheet = false
    /// Set in the reading face the app carries, whose rule `dress` writes ahead of the look.
    var readingFace = false
    /// The pane's paper under a mail that lies on it, which a frame that can't be clear paints itself; nil for a
    /// sheet, which paints its own.
    var paper: String?

    /// The line's length (the mockup's `--measure`): the text's size times as many of its widths as the setting says.
    static func measure(_ width: API.ReadWidth, size: API.ReadSize) -> CGFloat {
        let widths: Double = switch width {
        case .narrow: 33
        case .medium: 40
        case .wide: 50
        }
        return CGFloat((Self.size(size) * widths).rounded())
    }

    /// The text's sizes, the mockup's four: 15, 17 (the one with nothing said), 19 and 21.
    static func size(_ size: API.ReadSize) -> Double {
        switch size {
        case .s: 15
        case .m: 17
        case .l: 19
        case .xl: 21
        }
    }

    /// How far apart the lines stand (`.prose`'s 1.72); a monospaced face sits closer.
    static func lineHeight(_ font: API.ReadFont) -> Double {
        font == .mono ? 1.55 : 1.72
    }

    /// The faces: Nunito Sans, which the frame is given in its own document (`FrameFont`), with the system's behind
    /// it; a serif and a monospaced face from the system.
    static func font(_ font: API.ReadFont) -> String {
        switch font {
        case .sans: #""\#(FrameFont.family)",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif"#
        case .serif: #""Iowan Old Style",Charter,"Source Serif 4",Georgia,serif"#
        case .mono: #"ui-monospace,"SF Mono",Menlo,Consolas,monospace"#
        }
    }

    /// Day's colours, which a look takes when it is given no other, and plain text in its own colours at night.
    static let light = Palette(ink: "#24221E", background: "#FFFDF8", link: "#2D4B70", quoteLine: "#D5C9B1",
                               quoteInk: "#655E50")

    /// The quoted text's fold (`.fold`, `.quoted`): a pill that says what it does, and the quote under a line.
    private static func quote(_ colors: Palette, face: String) -> String {
        "details.mm-quote{margin:1em 0 0}details.mm-quote>summary{cursor:pointer;list-style:none;display:inline-flex;"
            + "align-items:center;height:28px;padding:0 10px;box-sizing:border-box;border-radius:999px;"
            + "border:1px solid \(colors.quoteLine);background:\(colors.raised);color:\(colors.quoteInk);"
            + "font:700 12px/1 \(face)}details.mm-quote>summary::-webkit-details-marker{display:none}"
            + "details.mm-quote .mm-hide,details.mm-quote[open] .mm-show{display:none}"
            + "details.mm-quote[open] .mm-hide{display:inline}details.mm-quote>pre{margin-top:10px;"
            + "padding-left:14px;border-left:2px solid \(colors.quoteLine);color:\(colors.quoteInk);font-size:.9em}"
            // The core's fold of a rich mail (`mm_content`'s `FOLD_OPEN`) says only "··· quoted text", open or
            // closed: its words go undrawn, and the plain fold's are drawn in their place, Show or Hide as it stands.
            + "details.mm-quote>summary:not(:has(.mm-show)){font-size:0}"
            + "details.mm-quote>summary:not(:has(.mm-show))::after{content:\"\(PlainQuote.show)\";font-size:12px}"
            + "details.mm-quote[open]>summary:not(:has(.mm-show))::after{content:\"\(PlainQuote.hide)\"}"
    }

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
    /// mail takes and `light` the one it takes otherwise: the light's own. Plain text is set as `.prose` sets it, in
    /// the reading face and size; rich mail keeps the sender's own type, and where it sets none, reads in the reading
    /// face at the size with nothing said, whatever the settings say for plain text.
    static func of(html: Bool, prefs: Prefs, darkAppearance: Bool, dark: Palette, light: Palette = MailLook.light)
        -> MailLook
    {
        let darkened = isDark(html: html, prefs: prefs, darkAppearance: darkAppearance)
        // A mail as dark as the light lies on the pane's paper; one lighter or darker than it is a sheet of its own.
        let sheet = darkened != darkAppearance
        let colors = darkened ? dark : darkAppearance ? Self.light : light
        let back = sheet ? colors.background : "transparent", margin = sheet ? 14 : 0
        // Whichever palette a mail on the paper takes, the paper under it is the light's own.
        let paper = sheet ? nil : light.background
        let set = html ? Prefs(colors: prefs.colors) : prefs
        let size = Self.size(set.size)
        let px = size == size.rounded() ? String(Int(size)) : String(size)
        let face = font(.sans), sans = set.font == .sans
        let type = "font:\(px)px/\(lineHeight(set.font)) \(font(set.font))"
        let media = "img{max-width:100%;height:auto;max-height:8000px}table{max-width:100%}"
        // The body holds its first and last children's margins, so the height the frame measures takes them in and
        // a mail that opens on a heading isn't cut short at its foot.
        let holds = "display:flow-root"
        // Invert the mail to darken its usually light backgrounds, and invert media back so photos stay true.
        if html && darkened {
            return MailLook(css: "html{background:\(back)}body{margin:\(margin)px;\(type);overflow-wrap:break-word;"
                + "\(holds);filter:invert(.92) hue-rotate(180deg)}body img,body picture,body video,body svg,"
                + "body [style*=background-image]{filter:invert(1) hue-rotate(180deg)}\(media)"
                + quote(dark, face: face), inverted: true, sheet: sheet, readingFace: sans, paper: paper)
        }
        if html && sheet {
            // Its own light colours at night, as a printed page would be.
            return MailLook(css: "html{background:#ffffff}body{margin:14px;\(type);color:#1a1a1a;"
                + "overflow-wrap:break-word;\(holds)}a{color:#0b66c3}\(media)"
                + "blockquote{border-left:3px solid #ccc;margin:0 0 0 4px;padding-left:12px;color:#555}"
                + quote(Self.light, face: face), inverted: false, sheet: true, readingFace: sans)
        }
        let prose = "html{background:\(back)}body{margin:\(margin)px;\(type);color:\(colors.ink);"
            + "overflow-wrap:break-word;-webkit-font-smoothing:antialiased;\(holds)}a{color:\(colors.link)}"
            + "blockquote{border-left:2px solid \(colors.quoteLine);margin:0 0 0 2px;padding-left:14px;"
            + "color:\(colors.quoteInk)}"
        if html {
            return MailLook(css: prose + media + quote(colors, face: face), inverted: false, sheet: sheet,
                            readingFace: sans, paper: paper)
        }
        return MailLook(css: prose + "pre{white-space:pre-wrap;font:inherit;margin:0}" + quote(colors, face: face),
                        inverted: false, sheet: sheet, readingFace: sans, paper: paper)
    }

    /// The core's document for a frame with this look in its head: the reading face's rule when it is set in it,
    /// then the look. The style is ours, never the mail's; the document's policy allows an inline style and a font
    /// from a data: URL, and nothing here asks for more. The mail's own HTML is in the body, after the head.
    /// `clear`: whether the frame shows what lies under it. One that can't paints the pane's paper itself, so a
    /// night's light ink never sits on the web view's own white.
    func dress(_ document: String, clear: Bool = true) -> String {
        guard let head = document.range(of: "</head>") else { return document }
        var dressed = document
        let under = clear ? "" : paper.map { "html{background:\($0)}" } ?? ""
        dressed.insert(contentsOf: "<style>\(readingFace ? FrameFont.face : "")\(css)\(under)</style>",
                       at: head.lowerBound)
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

    /// What the fold's pill says, closed and open.
    static let show = "··· Show quoted text", hide = "··· Hide quoted text"

    /// The fold's pill, which says Show or Hide as the fold stands, with no script.
    static let summary = "<summary><span class=\"mm-show\">\(show)</span><span class=\"mm-hide\">\(hide)</span></summary>"

    /// The core's document of a plain mail (`frame::of_text`: its text whole and escaped in one `<pre>`) with its
    /// quote folded into a `<details>`, which opens and closes with no script. Any other document is left as it is.
    static func foldDocument(_ document: String) -> String {
        let open = "<body><pre>", close = "</pre></body></html>"
        guard let start = document.range(of: open), document.hasSuffix(close),
              start.upperBound <= document.index(document.endIndex, offsetBy: -close.count) else { return document }
        let inner = String(document[start.upperBound..<document.index(document.endIndex, offsetBy: -close.count)])
        guard let fold = fold(unescaped(inner)) else { return document }
        return String(document[..<start.upperBound]) + escaped(fold.head) + "</pre><details class=\"mm-quote\">"
            + summary + "<pre>" + escaped(fold.quoted) + close
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

/// The colours a mail takes in each of Meadow's lights: the paper's ink, link and lines, on the pane's own paper.
enum ReaderPalette {
    /// A colour as CSS writes it, in sRGB.
    static func css(_ color: Color) -> String {
        guard let srgb = NSColor(color).usingColorSpace(.sRGB) else { return "#000000" }
        let parts = [srgb.redComponent, srgb.greenComponent, srgb.blueComponent].map { Int(($0 * 255).rounded()) }
        if srgb.alphaComponent < 1 {
            return "rgba(\(parts[0]),\(parts[1]),\(parts[2]),\(String(format: "%.2f", srgb.alphaComponent)))"
        }
        return String(format: "#%02X%02X%02X", parts[0], parts[1], parts[2])
    }

    /// A mail's colours on `palette`'s paper.
    static func of(_ palette: MeadowPalette) -> MailLook.Palette {
        MailLook.Palette(ink: css(palette.ink), background: css(palette.paper), link: css(palette.primaryInk),
                         quoteLine: css(palette.line2), quoteInk: css(palette.ink3), raised: css(palette.paperRaised))
    }

    /// Night's colours, or Day's.
    static func of(dark: Bool) -> MailLook.Palette {
        of(dark ? MeadowPalette.night : MeadowPalette.day)
    }

    /// The look of one message in a light: a mail shown dark takes Night's colours, any other the light's own.
    static func look(html: Bool, prefs: MailLook.Prefs, palette: MeadowPalette, dark: Bool) -> MailLook {
        MailLook.of(html: html, prefs: prefs, darkAppearance: dark, dark: of(dark: true), light: of(palette))
    }

    /// The look of one message in the window's appearance, where no light is at hand (the composer's quote).
    static func look(html: Bool, prefs: MailLook.Prefs, scheme: ColorScheme) -> MailLook {
        look(html: html, prefs: prefs, palette: scheme == .dark ? .night : .day, dark: scheme == .dark)
    }
}
#endif
