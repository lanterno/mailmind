#if os(macOS)
import Foundation

/// The reading face in a mail's frame (#44): Nunito Sans, as the page sets mail.
///
/// The web view draws in a process of its own, which the faces the app registers for itself never reach, and the
/// frame's document may load nothing: its policy (the core's `mail_frame_policy`, which the frame's scheme hands on
/// unchanged) allows a font from a data: URL and from nowhere else. So the face comes inside the document, in the
/// `<style>` the look writes into its head: the app's own file, read once, as a data: URL.
///
/// That was chosen over a scheme of the app's that would serve its font files because it opens nothing: no scheme,
/// route or handler is added for the mail's page to name, no request leaves the document, and the policy stays the
/// core's, word for word. What it costs is the file's bytes in each frame's document. Only the upright file goes in:
/// mail's few italics are slanted from it. A file that can't be read, or isn't a font, leaves the system's face,
/// which the stack names next.
enum FrameFont {
    /// The family the look's stack names first: a name of the app's own, which no mail's CSS expects.
    static let family = "MailMind Read"
    /// The app's file, as `MeadowFonts.files` names it.
    static let file = "NunitoSans-Regular"

    /// The rule, made once from the app's file: empty when there is none.
    static let face: String = {
        guard let url = Bundle(for: Token.self).url(forResource: file, withExtension: "ttf"),
              let data = try? Data(contentsOf: url, options: .mappedIfSafe) else { return "" }
        return rule(data)
    }()

    /// `@font-face` for a file's bytes, every weight the variable font has, or nothing for bytes that aren't a
    /// TrueType font: whatever the file became, only a font goes into a mail's document.
    static func rule(_ data: Data) -> String {
        guard isTrueType(data) else { return "" }
        return "@font-face{font-family:\"\(family)\";src:url(data:font/ttf;base64,\(data.base64EncodedString()))"
            + " format(\"truetype\");font-weight:200 1000;font-style:normal}"
    }

    /// A TrueType file's first four bytes: version 1.0, or Apple's `true`.
    static func isTrueType(_ data: Data) -> Bool {
        let head = Array(data.prefix(4))
        return data.count > 12 && (head == [0, 1, 0, 0] || head == Array("true".utf8))
    }

    /// The bundle the font is built into: the app's, or the one a test runs in.
    private final class Token {}
}
#endif
