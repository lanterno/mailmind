#if os(macOS)
import Foundation

/// What a pasted link comes to with what follows a click taken off it, as the page's `compose/linkClean.ts`: a
/// redirector's wrapper, and tracking parameters. Pure.
enum LinkClean {
    /// Names that do nothing but attribute a click. A short, broad one (ref, source, si) could mean something to the
    /// site, so it stays.
    private static let tracking = try! NSRegularExpression(
        pattern: "^(?:utm_[a-z0-9_]+|fbclid|gclid|dclid|gbraid|wbraid|msclkid|mc_cid|mc_eid|igshid|_hsenc|_hsmi|mkt_tok)$",
        options: [.caseInsensitive])

    /// Redirectors whose target is plainly in the query, so nothing has to be asked to read it.
    private static let wrappers: [(host: String, path: String, params: [String])] = [
        (#"^(?:www\.)?google\.(?:[a-z]{2,3}|co\.[a-z]{2}|com\.[a-z]{2})$"#, "^/url$", ["q", "url"]),
        (#"^lm?\.facebook\.com$"#, #"^/l\.php$"#, ["u"]),
        (#"^l\.instagram\.com$"#, "^/$", ["u"]),
        (#"^(?:www\.)?linkedin\.com$"#, #"^/(?:redir/redirect|safety/go)/?$"#, ["url"]),
        (#"\.safelinks\.protection\.outlook\.com$"#, "^/$", ["url"]),
        (#"^(?:www\.)?youtube\.com$"#, "^/redirect$", ["q"]),
        (#"^(?:www\.)?slack-redir\.net$"#, "^/link$", ["url"]),
    ]
    /// A redirector can wrap another: this far and no further.
    private static let unwrapMax = 4

    private static let inText = try! NSRegularExpression(pattern: #"\bhttps?://[^\s<>"'`]+"#, options: [.caseInsensitive])
    /// Punctuation after a link, which is the sentence's.
    private static let trailing = Set(".,;:!?)]}。、，！？）】》」』»…")

    private static func target(of link: String) -> String? {
        guard let parts = URLComponents(string: link), let host = parts.host?.lowercased() else { return nil }
        let path = parts.percentEncodedPath.isEmpty ? "/" : parts.percentEncodedPath
        guard let wrapper = wrappers.first(where: {
            host.range(of: $0.host, options: .regularExpression) != nil
                && path.range(of: $0.path, options: .regularExpression) != nil
        }) else { return nil }
        for name in wrapper.params {
            guard let value = parts.percentEncodedQueryItems?.first(where: { $0.name == name })?.value else { continue }
            // As a form reads it: a plus is a space.
            let target = value.replacingOccurrences(of: "+", with: " ").removingPercentEncoding ?? value
            if target.range(of: #"^https?://\S+$"#, options: [.regularExpression, .caseInsensitive]) != nil,
               URL(string: target) != nil
            {
                return target
            }
        }
        return nil
    }

    /// Cut out of the query, so the rest of the link stays as it was written and a signature in it still holds.
    private static func withoutTracking(_ link: String) -> String {
        let hash = link.firstIndex(of: "#")
        let head = hash.map { String(link[..<$0]) } ?? link, tail = hash.map { String(link[$0...]) } ?? ""
        guard let q = head.firstIndex(of: "?") else { return link }
        let params = head[head.index(after: q)...].split(separator: "&", omittingEmptySubsequences: false).map(String.init)
        let kept = params.filter { param in
            let name = String(param.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false).first ?? "")
            return tracking.firstMatch(in: name, range: NSRange(name.startIndex..., in: name)) == nil
        }
        if kept.count == params.count { return link }
        return String(head[..<q]) + (kept.isEmpty ? "" : "?" + kept.joined(separator: "&")) + tail
    }

    private static func cleaned(_ url: String) -> String {
        var link = url
        for _ in 0..<unwrapMax {
            guard let next = target(of: link) else { break }
            link = next
        }
        return withoutTracking(link)
    }

    /// Every link in `text`, cleaned; the rest of it as it was.
    static func clean(_ text: String) -> String {
        var out = "", from = text.startIndex
        for match in inText.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
            guard let range = Range(match.range, in: text) else { continue }
            out += text[from..<range.lowerBound]
            var found = String(text[range])
            var after = ""
            while let last = found.last, trailing.contains(last) {
                after = String(last) + after
                found.removeLast()
            }
            out += cleaned(found) + after
            from = range.upperBound
        }
        out += text[from...]
        return out
    }
}
#endif
