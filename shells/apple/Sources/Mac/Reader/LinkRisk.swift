#if os(macOS)
import Foundation

/// Whether a link clicked in mail deserves a warning before it opens, as the page's `mailbox/linkRisk.ts` decides it,
/// in the same words. Pure: it reads the link's href and text and the sender's domain, and nothing else.
///
/// It reads two things the page's doesn't yet: the whole name a link's words give, where the page's stops after three
/// labels (`www.bank.co.uk` read as `bank.co`), and example.test, the fictional mail's own, as a suffix each
/// organisation has a name under.
enum LinkRisk {
    /// The labels under which a country gives organisations a second level of their own (`bank.co.uk`).
    private static let secondLevel: Set<String> = [
        "ac", "co", "com", "edu", "go", "gob", "gouv", "gov", "k12", "ltd", "me", "mil", "ne", "net", "nom", "or", "org",
        "plc", "sch",
    ]

    /// Suffixes of two labels the rule above misses, each as the public suffix list has it, and example.test, under
    /// which every organisation of the fictional mail (tests, demos) has a name of its own. A label is kept here with
    /// its country, not on its own: `ad.jp` is a suffix where `ad.nl` is a newspaper.
    private static let twoLabels: Set<String> = [
        "example.test", "nhs.uk", "police.uk", "govt.nz", "school.nz", "asn.au", "id.au", "ad.jp", "ed.jp", "gr.jp",
        "lg.jp", "re.kr", "pe.kr", "gv.at", "in.th", "mi.th", "idv.hk", "idv.tw", "per.sg", "muni.il", "firm.in",
        "gen.in", "ind.in", "nic.in", "res.in",
    ]

    /// The domain an organisation registers: the last two labels of a host, or three under a suffix of two labels
    /// (co.uk, com.au, police.uk, example.test).
    static func registrable(_ host: String) -> String {
        let labels = host.lowercased().split(separator: ".", omittingEmptySubsequences: false).map(String.init)
        let tail = labels.suffix(2)
        let top = tail.count == 2 ? tail.last : nil
        let second = tail.count == 2 ? tail.first : nil
        let underCountry = top?.count == 2 && second.map { secondLevel.contains($0) } == true
        let keep = underCountry || twoLabels.contains(tail.joined(separator: ".")) ? 3 : 2
        return labels.suffix(keep).joined(separator: ".")
    }

    /// A URL's host as the person would read it: lowercased, without `www.`; nil for what isn't a URL.
    static func host(of link: String) -> String? {
        guard let url = URL(string: link), url.scheme != nil, let host = url.host(percentEncoded: false),
              !host.isEmpty else { return nil }
        return withoutWWW(host.lowercased())
    }

    private static func withoutWWW(_ host: String) -> String {
        host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
    }

    private static let urlInside = try! NSRegularExpression(pattern: "https?://([a-z0-9.-]+)", options: [.caseInsensitive])

    /// The href's own host, and the host of any http(s) URL inside it: legitimate redirectors put it there.
    static func linkHosts(_ href: String) -> [String] {
        var hosts: [String] = []
        if let own = host(of: href) { hosts.append(own) }
        let decoded = href.removingPercentEncoding ?? href   // a stray % leaves it as it was
        let range = NSRange(decoded.startIndex..., in: decoded)
        for match in urlInside.matches(in: decoded, range: range) {
            if let found = Range(match.range(at: 1), in: decoded) {
                hosts.append(withoutWWW(decoded[found].lowercased()))
            }
        }
        return hosts
    }

    private static func hostHolds(_ claimed: String, _ hosts: [String]) -> Bool {
        hosts.contains { $0 == claimed || $0.hasSuffix("." + claimed) || claimed.hasSuffix("." + $0) }
    }

    private static let sensitive = try! NSRegularExpression(
        pattern: #"\b(sign[ -]?in|log[ -]?in|verify|confirm|account|password|reset|unlock|billing|invoice|payment|suspend)\b"#,
        options: [.caseInsensitive])
    /// A name in a link's words, whole: as many labels as it has, ending in letters.
    private static let named = try! NSRegularExpression(
        pattern: #"\b((?:[a-z0-9-]+\.)+[a-z]{2,})\b"#, options: [.caseInsensitive])

    private static func domain(of address: String) -> String {
        let after = address.lastIndex(of: "@").map { address[address.index(after: $0)...] } ?? Substring(address)
        var domain = after.trimmingCharacters(in: .whitespaces)
        if domain.hasSuffix(".") { domain.removeLast() }
        return domain
    }

    /// Whether an answer to `sender` would go to `replyTo` outside the sender's organisation: a subdomain of it, or
    /// another under the domain it registers, is still theirs.
    static func leavesOrganisation(_ replyTo: String, _ sender: String) -> Bool {
        let to = domain(of: replyTo), from = domain(of: sender)
        return !to.isEmpty && !from.isEmpty && registrable(to) != registrable(from)
    }

    /// Why a link deserves a warning, in words, or nil.
    static func risk(href: String, text: String, senderDomain: String?) -> String? {
        guard href.range(of: "^https?:", options: [.regularExpression, .caseInsensitive]) != nil,
              let host = host(of: href) else { return nil }
        // A host of other scripts is shown as Unicode here, where the page's URL shows it as punycode: either way it
        // is the same warning.
        if host.range(of: "(^|\\.)xn--", options: [.regularExpression, .caseInsensitive]) != nil
            || host.unicodeScalars.contains(where: { !$0.isASCII })
        {
            return "a non-Latin “punycode” domain (\(host)), often used to imitate a real site"
        }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let range = NSRange(trimmed.startIndex..., in: trimmed)
        if let match = named.firstMatch(in: trimmed, range: range), let found = Range(match.range(at: 1), in: trimmed) {
            let claimed = withoutWWW(trimmed[found].lowercased())
            // Only a real host in the URL clears it, not a substring planted in a path, a query or a subdomain.
            return hostHolds(claimed, linkHosts(href))
                ? nil : "a different domain (\(host)) than the link text suggests (\(claimed))"
        }
        if let sender = senderDomain, !sender.isEmpty,
           sensitive.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil,
           registrable(host) != registrable(sender.lowercased())
        {
            return "an unfamiliar domain (\(host)) — the text looks like a sign-in/account action but the sender is \(sender)"
        }
        return nil
    }
}
#endif
