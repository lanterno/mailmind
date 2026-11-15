#if os(macOS)
import Foundation

/// The composer's send-time checks, as the page's `compose/sendGuard.ts` makes them, in its words: pure, with no
/// screen and no app state.
enum SendGuard {
    static let fanout = 5
    /// How long a warning shows before Send anyway may be pressed: a double click or a held key never confirms.
    static let armSeconds = 0.7
    static let scanMax = 50_000

    /// What the checks read of a composer. Addresses are lowercased bare addresses. Bcc is left out: it's hidden from
    /// the others and typed on purpose.
    struct Snapshot: Equatable {
        var mode: API.ComposeMode
        var subject: String
        var subject0: String
        var body: String
        var sigBlock: String
        var nAttach: Int
        var fwdAttach: Int
        var reading: [String]
        var sendAfter: Int
        var now: Int
        var to: [String]
        var cc: [String]
        var mine: [String]
        var autoCc: [String]?
        var fresh: [String]
        var near: [NearMiss] = []
        var redirect: Redirect?
        var ack: Acks

        struct Redirect: Equatable {
            var to: String
            var sender: String
            var stranger: Bool
        }
    }

    /// What a person has already waved through for this message.
    struct Acks: Equatable {
        var subject = false
        var attach = false
        var fanout = 0
        var near: [String] = []
        var redirect = false
        var unchecked = false
    }

    struct NearMiss: Equatable, Hashable {
        var addr: String
        var near: String
    }

    // MARK: Yours

    /// you+tag@host names the mailbox you@host does, on every server that reads a tag at all.
    static func untagged(_ address: String) -> String {
        guard let at = address.lastIndex(of: "@"), let plus = address.firstIndex(of: "+"),
              plus > address.startIndex, plus < at else { return address }
        return String(address[..<plus]) + String(address[at...])
    }

    static func isMine(_ address: String, _ mine: Set<String>) -> Bool {
        mine.contains(address) || mine.contains(untagged(address))
    }

    /// Your addresses as a set to ask `isMine` of. An account that is itself tagged has its plain mailbox along with it.
    static func mineSet(_ addresses: [String]) -> Set<String> {
        Set(addresses.map { $0.lowercased() }.flatMap { [$0, untagged($0)] })
    }

    // MARK: The attachment word

    // Sources are in folded form (NFD, marks removed, lowercase). Strong words present a file of your own ("attached",
    // "ci-joint", "te adjunto"), so they fire in every mode. Weak ones are the bare nouns ("the attachment", "la pièce
    // jointe", "l'allegato"), which in a reply are usually about the other person's file, so they fire only in a new
    // message or a forward. A lookbehind sits behind a lookahead for its word, so it only runs where the word is.
    private static let strong = [
        "attached", "attaching", "enclosed", "enclosing",
        #"en pieces? jointes?"#, #"ci[-\s]?jointe?s?"#, "en annexe", #"je (?:vous |te )?joins"#,
        #"nous (?:vous |te )?joignons"#,
        #"(?:veuillez trouver|trouverez|trouveras) (?:en |la |les )?(?:pieces? jointes?|pj)"#,
        #"(?:te|le|les|os) adjunt(?:o|amos)"#, "adjuntamos", "adjuntad[oa]s?",
        #"(?=adjunt)(?<=(?:^|[\n.!?:;,¡]) {0,3})adjunt(?:o|a|os|as)"#,   // "Adjunto el informe", not "directora adjunta"
        #"(?:mando|envio|remito|paso|enviamos|mandamos|remitimos|va|van|encontraras|encontrara|encontrareis|encontraran) adjunt[oa]s?"#,
        #"(?:archivos?|documentos?|ficheros?|informes?|facturas?|presupuestos?|contratos?|curriculum|fotos?|imagen(?:es)?) adjunt[oa]s?"#,
        "em anexo", #"(?:segue|seguem|envio|enviamos|mando|mandamos|encaminho|remeto|va|van) anex[oa]s?"#,
        "anexad[oa]s?", "anexei", "anexamos",
        "anbei", "im anhang", "beigefugt", "angehangt", "angefugt", "beiliegend", "(?=anliegend)(?<!eng |enger )anliegend",
        #"(?:in der|als) anlage (?:finden|erhalten|sende|senden|schicke|schicken|ubersende|ubersenden)"#,   // not "Gold als Anlage"
        "in allegato", #"(?=allegat)(?<!['’]|gli |un |tuoi |suoi |vostri |loro )allegat[oaie]"#, "allego", "alleghiamo",
        "bijgevoegd", "bijgesloten", "bijgaand", "in de bijlage", "als bijlage",
        #"[وف]?[بل]?(?:مرفق|ارفق|نرفق|ارفاق)[ء-ي]{0,3}"#,      // مرفق, أرفقت, نرفقها, إرفاق, with no article
        #"[وف]?(?:ب|في )ال?مرفق[ء-ي]{0,2}"#,                     // بالمرفق, في المرفق: "in the attachment"
        #"(?=المرفق)(?<=ال[ء-ي]{2,12} )المرفق(?:ة|ين|تين)?"#,    // الملف المرفق: "the attached file"
    ]
    private static let weak = [
        "attach", "attachments?", "enclosures?", "cover letter",
        "pieces? jointes?", "jointes?", "lettres? de motivation",
        "(?:el|los|un|unos|del|al|tu|tus|su|sus|mi|mis|este|estos|ese|esos) adjuntos?", "carta de presentacion",
        "anexos?", "anexar", "carta de apresentacao", "anhange?", "in der anlage", "begleitschreiben",
        "allegat[oi]", "lettera di accompagnamento", "bijlagen?", "sollicitatiebrief",
        #"[وفبلك]{0,2}(?:ال|ل)مرفق[ء-ي]{0,3}"#,                  // المرفقات: "the attachments"
    ]

    private static func words(_ list: [String]) -> NSRegularExpression {
        try! NSRegularExpression(pattern: #"(^|[^\p{L}\p{N}])("# + list.joined(separator: "|") + #")(?![\p{L}\p{N}])"#)
    }

    private static let strongWords = words(strong)
    private static let weakWords = words(weak)
    /// A file name (weak). "_" and "-" can't start one mid-run, so a long run of them stays fast.
    private static let fileName = try! NSRegularExpression(
        pattern: #"(^|[^\p{L}\p{N}_-])([\p{L}\p{N}_-]+\.(?:pdf|docx?|xlsx?|pptx?|odt|ods|odp|rtf|csv|zip))(?![\p{L}\p{N}])"#)
    // Case-sensitive, on unfolded text. A bare "PJ" is also a nickname, "pessoa jurídica" or "police judiciaire", so it
    // needs "en PJ", "voir PJ" or "PJ :" to be strong and an article to be weak.
    private static let pj = try! NSRegularExpression(
        pattern: #"(^|[^\p{L}\p{N}])((?:[Ee]n|[Vv]oir|[Cc]f\.?) PJ(?![\p{L}\p{N}])|PJ(?=\s{0,3}:))"#)
    private static let pjWeak = try! NSRegularExpression(
        pattern: #"(^|[^\p{L}\p{N}])((?:[Ll]a|[Ll]es|[Uu]ne|[Dd]es|[Mm]a|[Mm]es|[Vv]otre|[Vv]os|[Nn]otre|[Nn]os|[Cc]ette|[Cc]es) PJ)(?![\p{L}\p{N}])"#)
    private static let cv = try! NSRegularExpression(pattern: #"(^|[^\p{L}\p{N}])(CV)(?![\p{L}\p{N}])"#)
    private static let replyPrefix = try! NSRegularExpression(pattern: #"^\s*(?:(?:re|fwd?|fw)\s*:\s*)+"#,
                                                                options: [.caseInsensitive])

    /// Text folded for matching, and where each of its UTF-16 units came from in the original (`map[k]`), so a match
    /// echoes as it was typed.
    static func fold(_ text: String) -> (text: String, map: [Int]) {
        var folded = "", map: [Int] = [], at = 0
        for scalar in text.unicodeScalars {
            let bare = String(scalar).decomposedStringWithCanonicalMapping.unicodeScalars.filter {
                switch $0.properties.generalCategory {
                case .nonspacingMark, .spacingMark, .enclosingMark: false
                default: true
                }
            }
            let lower = String(String.UnicodeScalarView(bare)).lowercased()
            map.append(contentsOf: repeatElement(at, count: lower.utf16.count))
            folded += lower
            at += scalar.utf16.count
        }
        map.append(at)
        return (folded, map)
    }

    private static let line = try! NSRegularExpression(pattern: #"\r\n?"#)

    /// What you wrote: not the signature, not a quote.
    static func typedText(_ body: String, sigBlock: String) -> String {
        var text = line.stringByReplacingMatches(in: body, range: NSRange(body.startIndex..., in: body), withTemplate: "\n")
        var sig = Substring(sigBlock)
        while sig.first == "\n" { sig = sig.dropFirst() }
        if !sig.isEmpty, let at = text.range(of: sig, options: .backwards),
           at.lowerBound == text.startIndex || text[text.index(before: at.lowerBound)] == "\n"
        {
            text.removeSubrange(at)   // a P.S. below it is still yours
        }
        var kept: [String] = []
        for line in text.components(separatedBy: "\n") {
            if matches(line, #"^-- ?$"#) { break }   // the signature's separator
            if matches(line, #"^On .{0,300}wrote:\s*$"#) { break }   // a reply's attribution
            if matches(line, #"^-{5,} ?Forwarded message ?-{5,}\s*$"#, caseless: true) { break }
            if matches(line, #"^-{3,} ?Original Message ?-{3,}\s*$"#, caseless: true) { break }   // a pasted Outlook quote
            if matches(line, #"^\s*>"#) { continue }
            kept.append(line)
        }
        let joined = kept.joined(separator: "\n")
        let trimmed = String(joined.reversed().drop(while: { $0.isWhitespace }).reversed())
        return String(trimmed.prefix(scanMax))
    }

    private static func matches(_ text: String, _ pattern: String, caseless: Bool = false) -> Bool {
        text.range(of: pattern, options: caseless ? [.regularExpression, .caseInsensitive] : .regularExpression) != nil
    }

    private static let space = try! NSRegularExpression(pattern: #"\s+"#)
    private static let link = try! NSRegularExpression(pattern: #"(?:https?|ftp)://.*|www\..*"#, options: [.caseInsensitive])

    /// Links and addresses out (attachment.cgi, attachments@…), a word at a time so it stays linear.
    static func scrub(_ text: String) -> String {
        var out = "", from = text.startIndex
        func word(_ part: Substring) {
            let part = String(part)
            if part.contains("@") {
                out += " "
            } else {
                out += link.stringByReplacingMatches(in: part, range: NSRange(part.startIndex..., in: part), withTemplate: " ")
            }
        }
        for gap in space.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
            guard let range = Range(gap.range, in: text) else { continue }
            if from < range.lowerBound { word(text[from..<range.lowerBound]) }
            out += text[range]
            from = range.upperBound
        }
        if from < text.endIndex { word(text[from...]) }
        return out
    }

    /// The attachment word as typed, or "".
    static func attachWord(_ text: String, allowWeak: Bool) -> String {
        let clean = scrub(text)
        let (folded, map) = fold(clean)
        let cleanUnits = clean as NSString, foldedUnits = folded as NSString
        func hit(_ regex: NSRegularExpression, folded isFolded: Bool) -> String {
            let source = isFolded ? foldedUnits : cleanUnits
            guard let match = regex.firstMatch(in: source as String, range: NSRange(location: 0, length: source.length))
            else { return "" }
            let word = match.range(at: 2)
            if !isFolded { return cleanUnits.substring(with: word) }
            let start = map[word.location], end = map[word.location + word.length]
            return cleanUnits.substring(with: NSRange(location: start, length: end - start))
        }
        let strongHit = hit(strongWords, folded: true)
        if !strongHit.isEmpty { return strongHit }
        let pjHit = hit(pj, folded: false)
        if !pjHit.isEmpty || !allowWeak { return pjHit }
        for (regex, isFolded) in [(weakWords, true), (fileName, true), (pjWeak, false), (cv, false)] {
            let found = hit(regex, folded: isFolded)
            if !found.isEmpty { return found }
        }
        return ""
    }

    private static func withoutReplyPrefix(_ subject: String) -> String {
        replyPrefix.stringByReplacingMatches(in: subject, range: NSRange(subject.startIndex..., in: subject),
                                             withTemplate: "")
    }

    /// A subject that says nothing. A reply's inherited bare "Re:" is not empty.
    static func subjectEmpty(_ subject: String, mode: API.ComposeMode) -> Bool {
        let trimmed = subject.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty
            || (mode == .new && withoutReplyPrefix(trimmed).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    /// Whether you wrote the subject: "Re: Contract attached" is the other sender's.
    static func subjectAuthored(_ subject: String, _ subject0: String, mode: API.ComposeMode) -> Bool {
        let folded = { (text: String) in
            fold(withoutReplyPrefix(text)).text.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return mode == .new || folded(subject) != folded(subject0)
    }

    /// How many people a message goes to, you aside.
    static func fanout(to: [String], cc: [String], mine: [String]) -> Int {
        let you = Set(mine)
        return Set((to + cc).filter { !$0.isEmpty && !isMine($0, you) }).count
    }

    static func list(_ items: [String]) -> String {
        switch items.count {
        case 0: ""
        case 1: items[0]
        case 2: "\(items[0]) and \(items[1])"
        case 3: "\(items[0]), \(items[1]) and \(items[2])"
        default: "\(items[0]), \(items[1]) and \(items.count - 2) more"
        }
    }

    // MARK: The checks

    static func check(_ s: Snapshot) -> [GuardWarning] {
        if let first = s.reading.first { return [.reading(name: first, more: s.reading.count - 1)] }
        var found: [GuardWarning] = []
        if s.sendAfter > 0 && s.sendAfter <= s.now + 5 { found.append(.late(when: s.sendAfter)) }
        if !s.ack.subject && subjectEmpty(s.subject, mode: s.mode) { found.append(.subject) }
        let has = s.nAttach > 0 || (s.mode == .forward && s.fwdAttach > 0)   // the server attaches a forward's files again
        if !has && !s.ack.attach {
            let weak = s.mode == .new || s.mode == .forward
            var word = attachWord(typedText(s.body, sigBlock: s.sigBlock), allowWeak: weak)
            if word.isEmpty && subjectAuthored(s.subject, s.subject0, mode: s.mode) {
                word = attachWord(s.subject, allowWeak: weak)
            }
            if !word.isEmpty { found.append(.attach(word: word)) }
        }
        if s.mode == .replyall {
            let n = fanout(to: s.to, cc: s.cc, mine: s.mine)
            if n > fanout && n > s.ack.fanout {
                let you = Set(s.mine)
                let removable = s.autoCc.map { auto in Set(s.cc.filter { auto.contains($0) && !isMine($0, you) }).count } ?? 0
                found.append(.fanout(n: n, removable: removable))
            }
        }
        let near = s.near.filter { !s.ack.near.contains($0.addr) }
        if !near.isEmpty { found.append(.near(pairs: near)) }
        var named = Set(near.map(\.addr))   // a line above names these already
        let you = Set(s.mine)
        if let r = s.redirect, s.to.contains(r.to), !s.to.contains(r.sender), !s.cc.contains(r.sender),
           !isMine(r.to, you), !isMine(r.sender, you)
        {
            found.append(.redirect(to: r.to, sender: r.sender, block: r.stranger && !s.ack.redirect ? .confirm : .soft))
            named.insert(r.to)
        }
        let fresh = s.fresh.filter { !named.contains($0) }
        if !fresh.isEmpty { found.append(.new(addrs: fresh)) }
        return found
    }

    static func blocks(_ warnings: [GuardWarning]) -> Bool { warnings.contains { $0.block != .soft } }
    static func hard(_ warnings: [GuardWarning]) -> Bool { warnings.contains { $0.block == .hard } }
}

/// How much a warning holds a send: not at all (soft), until it is waved through (confirm), or until it is fixed.
enum GuardBlock: String, Equatable, Sendable {
    case hard, confirm, soft
}

/// What a line's button does about it, and an address it swaps for another.
struct GuardFix: Equatable, Sendable {
    var kind: String
    var label: String
    var swap: Swap?

    struct Swap: Equatable, Sendable {
        var from: String
        var to: String
    }
}

/// A check's warning, or what the composer itself finds before the checks.
enum GuardWarning: Equatable, Sendable {
    case reading(name: String, more: Int)
    case late(when: Int)
    case subject
    case attach(word: String)
    case fanout(n: Int, removable: Int)
    case near(pairs: [SendGuard.NearMiss])
    case redirect(to: String, sender: String, block: GuardBlock)
    case new(addrs: [String])
    case unchecked
    case norcpt
    case bad(value: String, more: Int, field: ComposeField)

    var key: String {
        switch self {
        case .reading: "reading"
        case .late: "late"
        case .subject: "subject"
        case .attach: "attach"
        case .fanout: "fanout"
        case .near: "near"
        case .redirect: "redirect"
        case .new: "new"
        case .unchecked: "unchecked"
        case .norcpt: "norcpt"
        case .bad: "bad"
        }
    }

    /// How many people a fan-out line counted.
    var fanout: Int? {
        if case .fanout(let n, _) = self { return n }
        return nil
    }

    var block: GuardBlock {
        switch self {
        case .reading, .late, .norcpt, .bad: .hard
        case .subject, .attach, .fanout, .near, .unchecked: .confirm
        case .redirect(_, _, let block): block
        case .new: .soft
        }
    }

    /// The line a person reads, and its fix. `when` words a late time.
    func line(when: (Int) -> String = { String($0) }) -> (text: String, fix: GuardFix?) {
        switch self {
        case .reading(let name, let more):
            return ("Still adding \(name)\(more > 0 ? " and \(more) more" : ""). Send again once \(more > 0 ? "they’re" : "it’s") added.", nil)
        case .late(let at):
            return ("The time you picked, \(when(at)), has passed.", GuardFix(kind: "time", label: "Pick a new time"))
        case .subject:
            return ("There’s no subject.", GuardFix(kind: "subject", label: "Add a subject"))
        case .attach(let word):
            return ("You wrote “\(word)”, but nothing is attached.", GuardFix(kind: "attach", label: "Attach a file"))
        case .fanout(let n, let removable):
            return ("This reply goes to \(n) people.", removable > 0
                ? GuardFix(kind: "trim", label: "Remove the \(removable) copied from the original")
                : GuardFix(kind: "review", label: "Review recipients"))
        case .near(let pairs):
            let first = pairs[0], more = pairs.count - 1
            let rest = more > 0 ? " \(more) more \(more == 1 ? "looks" : "look") the same." : ""
            return ("“\(first.addr)” is one typo away from \(first.near), someone you’ve written to.\(rest)",
                    GuardFix(kind: "near", label: "Use \(first.near)", swap: .init(from: first.addr, to: first.near)))
        case .redirect(let to, let sender, _):
            return ("Replies go to \(to), not to the sender, \(sender).",
                    GuardFix(kind: "sender", label: "Reply to the sender instead", swap: .init(from: to, to: sender)))
        case .new(let addrs):
            return ("New to you: \(SendGuard.list(addrs)).", nil)
        case .unchecked:
            return ("The checks before sending couldn’t run.", nil)
        case .norcpt:
            return ("Add at least one recipient.", GuardFix(kind: "to", label: "Add one"))
        case .bad(let value, let more, _):
            let rest = more > 0 ? " \(more) more \(more == 1 ? "needs" : "need") fixing too." : ""
            return ("“\(value)” isn’t a valid address.\(rest)", GuardFix(kind: "bad", label: "Edit it"))
        }
    }
}
#endif
