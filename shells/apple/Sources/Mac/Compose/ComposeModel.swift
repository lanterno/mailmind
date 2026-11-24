#if os(macOS)
import Foundation

/// What a composer is, with no screen and no server, as the page's `compose/model.ts` has it: addresses, the seeds a
/// reply or forward opens with, and the snippets' words. The composer beside it runs these.

/// A field of recipients.
enum ComposeField: String, CaseIterable, Hashable, Sendable {
    case to, cc, bcc
}

/// A recipient as typed: `bad` when it isn't an address.
struct Recipient: Hashable, Sendable {
    var value: String
    var bad: Bool

    /// A chip from what was typed.
    init(_ value: String) {
        self.value = value
        bad = !Addresses.looksLikeAddress(value)
    }

    /// A chip the server or the original named, taken as it is.
    init(trusted value: String) {
        self.value = value
        bad = false
    }

    /// Where its mail goes.
    var address: String { Addresses.recipient(value) }
}

/// A file going with the message, read in full. `cid`: the token its picture's mark in the text names it by.
struct ComposeFile: Hashable, Sendable {
    var filename: String
    var contentType: String
    var size: Int
    var base64: String
    var cid: String?

    var json: JSONValue {
        var fields: [String: JSONValue] = ["filename": .string(filename), "content_type": .string(contentType),
                                           "b64": .string(base64)]
        if let cid { fields["cid"] = .string(cid) }
        return .object(fields)
    }
}

/// The message a reply or forward quotes.
struct Quote: Hashable, Sendable {
    var html: String?
    var text: String
    var from: String = ""
    var date: String = ""
}

/// A Reply-To that sent the reply to `to`, not to who wrote the mail.
struct Redirect: Hashable, Sendable {
    var to: String
    var sender: String
}

/// What the lookup said of an address: someone you've written to, yourself, or a typo of `near`.
struct Person: Hashable, Sendable {
    var known: Bool
    var isSelf: Bool
    var near: String?
}

/// A reopened draft's own threading and quote, sent back with each save and the send.
struct ResumeFields: Hashable, Sendable {
    var inReplyTo: String
    var references: String
    var tailText: String

    var json: [String: JSONValue] {
        ["in_reply_to": .string(inReplyTo), "references": .string(references), "tail_text": .string(tailText)]
    }
}

/// What a composer opens with.
struct ComposeSeed: Sendable {
    var mode: API.ComposeMode = .new
    var from: String?
    var to: [String] = []
    var cc: [String] = []
    var bcc: [String] = []
    var subject: String = ""
    var subject0: String?
    var origKey: Int?
    var quote: Quote?
    var draftId: Int?
    var version: Int?
    var resume: ResumeFields?
    var fwdAttach = 0
    var noSignature = false
    var autoCc: [String]?
    var participants: [String] = []
    var ack = SendGuard.Acks()
    /// The text it opens with; nil writes the signature in.
    var body: String?
    var attachments: [ComposeFile] = []
    var note = ""
    var redirect: Redirect?
    /// When its words as they stand were saved: a reply closed in the reader's box, opened there again.
    var saved: Date?
    /// What the message replied to calls its people, by address: the window's head names whom a reply goes to.
    var names: [String: String] = [:]
}

/// Addresses as the composer reads them.
enum Addresses {
    private static let anywhere = try! NSRegularExpression(pattern: #"[^\s<>,;"]+@[^\s<>,;"]+"#)
    private static let bracketed = try! NSRegularExpression(pattern: #"<([^<>]+)>\s*$"#)
    private static let shape = try! NSRegularExpression(pattern: #"^[^\s@]+@[^\s@]+\.[^\s@]+$"#)

    /// The addresses in a header.
    static func parse(_ text: String?) -> [String] {
        let text = text ?? ""
        return anywhere.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap {
            Range($0.range, in: text).map { String(text[$0]) }
        }
    }

    static func first(_ text: String?) -> String {
        parse(text).first ?? ""
    }

    /// The people a header names, each as it was written ("Lin Mori <lin@example.test>"): split on the commas and
    /// semicolons between them, never on one inside a quoted name or the angle brackets.
    static func entries(_ text: String?) -> [String] {
        pieces(text).filter { !first($0).isEmpty }
    }

    /// Split on the commas and semicolons between people, never on one inside a quoted name or the angle brackets.
    private static func pieces(_ text: String?) -> [String] {
        var entries: [String] = [], entry = "", quoted = false, bracketed = false
        for character in text ?? "" {
            switch character {
            case "\"" where !bracketed: quoted.toggle()
            case "<" where !quoted: bracketed = true
            case ">" where !quoted: bracketed = false
            case ",", ";":
                if !quoted && !bracketed {
                    entries.append(entry)
                    entry = ""
                    continue
                }
            default: break
            }
            entry.append(character)
        }
        entries.append(entry)
        return entries.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
    }

    /// The name an entry was written with, quotes and all taken off, else nil.
    static func name(_ entry: String) -> String? {
        guard let open = entry.firstIndex(of: "<") else { return nil }
        let name = entry[..<open].trimmingCharacters(in: CharacterSet.whitespaces.union(CharacterSet(charactersIn: "\"'")))
        return name.isEmpty || name.contains("@") ? nil : name
    }

    /// Where a chip's mail goes: the <…> part, not an address in its name.
    static func recipient(_ value: String) -> String {
        let inBrackets = bracketed.firstMatch(in: value, range: NSRange(value.startIndex..., in: value))
            .flatMap { Range($0.range(at: 1), in: value) }.map { String(value[$0]) }
        let found = inBrackets ?? first(value)
        return (found.isEmpty ? value : found).trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    static func looksLikeAddress(_ value: String) -> Bool {
        let inner = value.range(of: #"<([^>]+)>"#, options: .regularExpression)
            .map { String(value[$0].dropFirst().dropLast()) } ?? value
        let bare = inner.trimmingCharacters(in: .whitespacesAndNewlines)
        return shape.firstMatch(in: bare, range: NSRange(bare.startIndex..., in: bare)) != nil
    }

    /// What was typed, split on commas and semicolons, but for one inside a quoted name (`"Mori, Ren" <ren@…>`).
    static func split(_ raw: String) -> [String] {
        pieces(raw).filter { !$0.isEmpty }
    }

    /// A person as a recipient is written with their name: `Lin Mori <lin@example.test>`, the name quoted where it
    /// holds what an address list reads (`"Mori, Ren" <ren@example.test>`); the address alone with no name.
    static func named(_ name: String, _ address: String) -> String {
        let name = oneLine(name)
        guard !name.isEmpty, !name.contains("@") else { return address }
        guard name.unicodeScalars.contains(where: nameSpecials.contains) else { return "\(name) <\(address)>" }
        let escaped = name.replacingOccurrences(of: #"\"#, with: #"\\"#).replacingOccurrences(of: #"""#, with: #"\""#)
        return #""\#(escaped)" <\#(address)>"#
    }

    /// What a name can't hold unquoted in an address list (RFC 5322's specials).
    private static let nameSpecials = CharacterSet(charactersIn: #"()<>[]:;,.\""#)

    /// A name on one line: each control character (a line break, a tab, a NUL) and each line or paragraph separator
    /// as a space, and a run of spaces as one. A name comes from other people's headers, and both servers refuse a
    /// header that runs over lines, so a name that kept one would fail the send.
    static func oneLine(_ name: String) -> String {
        let spaced = String(String.UnicodeScalarView(name.unicodeScalars.map { scalar -> Unicode.Scalar in
            switch scalar.properties.generalCategory {
            case .control, .lineSeparator, .paragraphSeparator: return " "
            default: return scalar
            }
        }))
        return spaced.split(separator: " ").joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Where a Reply-To sends a reply instead of to the mail's sender, or nil if it sends it to them.
    static func redirect(replyTo: String?, sender: String?) -> Redirect? {
        let to = first(replyTo).lowercased(), from = (sender ?? "").lowercased()
        return !to.isEmpty && !from.isEmpty && to != from ? Redirect(to: to, sender: from) : nil
    }

    /// A subject with a reply's or forward's prefix, once. Only a prefix of the same kind gives way: a reply to
    /// "Fwd: Fabric samples" is "Re: Fwd: Fabric samples", and a forward of "Re: Plans" is "Fwd: Re: Plans", as
    /// Mail has them.
    static func prefixed(_ subject: String?, _ prefix: String) -> String {
        let trimmed = (subject ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let same = prefix.lowercased().hasPrefix("re") ? "re" : "fwd?|fw"
        if trimmed.range(of: #"^(\#(same))\s*:"#, options: [.regularExpression, .caseInsensitive]) != nil {
            let rest = trimmed.replacingOccurrences(of: #"^(\#(same))\s*:\s*"#, with: "",
                                                    options: [.regularExpression, .caseInsensitive])
            return "\(prefix) \(rest)"
        }
        return trimmed.isEmpty ? prefix : "\(prefix) \(trimmed)"
    }
}

/// A recipient field's completion list, as the page's field keys it (`onKey` in compose/Composer.tsx): the server's
/// suggestions for what is typed, less the addresses the field holds, and the one highlighted. ↑ and ↓ move the
/// highlight round the list, Return or Tab takes it, and Esc takes the list away, not the composer. The first
/// suggestion stands highlighted while what is typed isn't an address yet, so Return takes the person the list offers
/// rather than a word that is no address; a whole address typed stands with none highlighted, so Return keeps it.
struct RecipientCompletion: Equatable {
    private(set) var items: [API.Suggestion] = []
    /// The suggestion highlighted, or -1 for none.
    private(set) var at = -1

    /// What Return or Tab takes.
    enum Taken: Equatable {
        /// The suggestion highlighted: its address with the person's name (`chip`), which its chip shows.
        case suggestion(String)
        /// What is typed, as it is.
        case typed
        /// Nothing: there is nothing typed, so the key is the field's own.
        case nothing
    }

    var shown: Bool { !items.isEmpty }

    /// The suggestion highlighted, if one is.
    var chosen: API.Suggestion? { items.indices.contains(at) ? items[at] : nil }

    /// What the server suggested for `typed`, without the addresses the field holds already.
    mutating func show(_ suggested: [API.Suggestion], for typed: String, holding: [Recipient]) {
        let have = Set(holding.map(\.address))
        items = suggested.filter { !have.contains($0.addr.lowercased()) }
        at = items.isEmpty || Addresses.looksLikeAddress(typed) ? -1 : 0
    }

    /// ↑ (-1) or ↓ (1): the highlight moves round the list. Nothing moves, and the key is the field's, without a list.
    mutating func move(_ by: Int) -> Bool {
        guard !items.isEmpty else { return false }
        at = (at + by + items.count + (at < 0 && by < 0 ? 1 : 0)) % items.count
        return true
    }

    /// What Return or Tab takes with `typed` in the field: the suggestion highlighted, else what is typed.
    func taken(typed: String) -> Taken {
        if let chosen { return .suggestion(Self.chip(chosen)) }
        return typed.trimmingCharacters(in: .whitespaces).isEmpty ? .nothing : .typed
    }

    /// The chip a suggestion makes: the person by the name the list showed, going to their address.
    static func chip(_ suggestion: API.Suggestion) -> String {
        Addresses.named(suggestion.name, suggestion.addr)
    }

    /// Esc: the list goes, and the key is taken only while there was one, so Esc never closes more than the list.
    mutating func escape() -> Bool {
        guard shown else { return false }
        close()
        return true
    }

    mutating func close() {
        items = []
        at = -1
    }
}

/// The seeds a reply and a forward open with.
enum Seeds {
    private static func quote(_ d: API.MessageOpened) -> Quote {
        Quote(html: d.hasHtml ? d.html : nil, text: d.text ?? "",
              from: [d.fromName, d.fromAddr].compactMap { $0 }.first { !$0.isEmpty } ?? "", date: d.date)
    }

    /// The people a message of your own went to, To before Cc, each once and none of them you: whom an answer to it
    /// goes to, as a follow-up goes on to them rather than back to you.
    static func theirs(_ d: API.MessageOpened, mine: Set<String>) -> (to: [String], cc: [String]) {
        guard SendGuard.isMine((d.fromAddr ?? "").lowercased(), mine) else { return ([], []) }
        var seen: Set<String> = []
        let others = { (header: String) in
            Addresses.parse(header).filter {
                let key = $0.lowercased()
                return !SendGuard.isMine(key, mine) && seen.insert(key).inserted
            }
        }
        return (others(d.to), others(d.cc))
    }

    static func reply(_ d: API.MessageOpened, all: Bool, mine: Set<String>) -> ComposeSeed {
        // Your own message (in Sent, or the latest of a conversation of yours) is answered to whom it went: its To
        // (its Cc where only they were others), and its Cc too for Reply All. One that went to no one but you is
        // answered as any other.
        let own = theirs(d, mine: mine)
        let followsUp = !own.to.isEmpty || !own.cc.isEmpty
        let toAddress = [Addresses.first(d.replyTo), d.fromAddr ?? ""].first { !$0.isEmpty } ?? ""
        var cc: [String] = []
        if followsUp {
            cc = all && !own.to.isEmpty ? own.cc : []
        } else if all {
            var seen: Set<String> = [toAddress.lowercased()]
            for address in Addresses.parse(d.to) + Addresses.parse(d.cc) {
                let key = address.lowercased()
                if !seen.contains(key) && !SendGuard.isMine(key, mine) {
                    seen.insert(key)
                    cc.append(address)
                }
            }
        }
        var seed = ComposeSeed()
        seed.mode = all ? .replyall : .reply
        seed.from = d.account
        seed.to = followsUp ? (own.to.isEmpty ? own.cc : own.to) : toAddress.isEmpty ? [] : [toAddress]
        seed.cc = cc
        seed.autoCc = cc
        // Not Reply-To: a redirect to a stranger gets marked.
        seed.participants = [d.fromAddr ?? ""] + Addresses.parse(d.to) + Addresses.parse(d.cc)
        // A Reply-To of your own message is yours: it sends nothing elsewhere.
        seed.redirect = followsUp ? nil : Addresses.redirect(replyTo: d.replyTo, sender: d.fromAddr)
        seed.subject = Addresses.prefixed(d.subject, "Re:")
        seed.quote = quote(d)
        seed.origKey = d.key
        seed.names = names(d)
        return seed
    }

    /// The names the message gives its people, by address: its sender's, and those in its To, Cc and Reply-To.
    static func names(_ d: API.MessageOpened) -> [String: String] {
        var names: [String: String] = [:]
        for entry in [d.to, d.cc, d.replyTo].flatMap(Addresses.entries) {
            if let name = Addresses.name(entry) { names[Addresses.recipient(entry)] = name }
        }
        if let from = d.fromAddr, let name = d.fromName.map(Addresses.oneLine), !name.isEmpty, !name.contains("@") {
            names[from.lowercased()] = name
        }
        return names
    }

    /// How many of the original's files a forward takes along: those the server marks, up to the budget it stops at.
    static func filesForwarded(_ attachments: [API.AttachmentPart], budget: Int?) -> Int {
        var count = 0, total = 0
        for file in attachments where file.travels {
            total += file.size
            if let budget, total > budget { break }
            count += 1
        }
        return count
    }

    static func forward(_ d: API.MessageOpened, budget: Int?) -> ComposeSeed {
        var seed = ComposeSeed()
        seed.mode = .forward
        seed.from = d.account
        seed.subject = Addresses.prefixed(d.subject, "Fwd:")
        seed.quote = quote(d)
        seed.fwdAttach = filesForwarded(d.attachments, budget: budget)
        seed.origKey = d.key
        return seed
    }

    /// A draft the server reopened (`draftResume.ts`): its fields, its own threading and quote, and no signature
    /// written in again.
    static func resumed(_ d: API.DraftOpened) -> ComposeSeed {
        var seed = ComposeSeed()
        seed.mode = d.mode
        seed.from = d.account
        seed.to = d.to
        seed.cc = d.cc
        seed.bcc = d.bcc
        seed.subject = d.subject
        seed.subject0 = d.subject
        seed.origKey = d.originalKey
        seed.draftId = d.draftId
        seed.version = d.version
        seed.participants = d.participants
        let tail = d.tailText
        if !tail.isEmpty {
            seed.quote = Quote(html: nil, text: String(tail.drop(while: { $0 == "\n" })))
        }
        seed.noSignature = true
        seed.resume = ResumeFields(inReplyTo: d.inReplyTo, references: d.references, tailText: tail)
        seed.body = d.text
        seed.attachments = d.files.map {
            ComposeFile(filename: $0.filename, contentType: $0.contentType, size: $0.size, base64: $0.b64, cid: $0.cid)
        }
        seed.note = resumeNotice(d.notes)
        seed.redirect = Addresses.redirect(replyTo: d.replyTo, sender: d.originalFrom)
        return seed
    }

    /// What a reply saved to a message says of the others saved to it besides (`moreReplies`).
    static func moreReplies(_ more: Int) -> String {
        if more <= 0 { return "" }
        return more == 1 ? "There’s 1 more draft reply to this message in Drafts."
            : "There are \(more) more draft replies to this message in Drafts."
    }

    /// What a reopened draft lost on the way, in words.
    static func resumeNotice(_ notes: API.DraftNotes) -> String {
        var said: [String] = []
        if notes.inline > 0 {
            said.append(notes.inline == 1 ? "1 picture in the text is now an attachment."
                : "\(notes.inline) pictures in the text are now attachments.")
        }
        if notes.bccDropped > 0 {
            said.append("The Bcc wasn’t restored, because this draft changed since MailMind saved it.")
        }
        if notes.plain {
            said.append("This draft opens as plain text, so its formatting won’t be kept when you save or send it.")
        }
        return said.joined(separator: " ")
    }

    /// The signature block a reopened text still ends with, or "".
    static func signatureKept(_ text: String, signature: String) -> String {
        let block = signature.isEmpty ? "" : "\n\n-- \n\(signature)"
        return !block.isEmpty && text.hasSuffix(block) ? block : ""
    }

    static func quoteLabel(mode: API.ComposeMode, fwdAttach: Int, open: Bool) -> String {
        (open ? "▾ " : "▸ ") + (mode == .forward ? "forwarded message" : "quoted message")
            + (fwdAttach > 0 ? " · \(fwdAttach) attachment\(fwdAttach > 1 ? "s" : "")" : "")
    }
}

/// The quick replies under a message (`QUICK`): a chip, the line it opens with, and what the AI is asked to write.
struct QuickReply: Hashable, Sendable {
    let key: String
    let chip: String
    let line: String
    let ask: String

    static let all = [
        QuickReply(key: "yes", chip: "Yes, that works", line: "Yes, that works for me.",
                   ask: "Say yes: agree to or confirm what they asked."),
        QuickReply(key: "no", chip: "Can’t, sorry", line: "Sorry, I can’t make that work.",
                   ask: "Politely say no to what they asked, without inventing a reason."),
        QuickReply(key: "thanks", chip: "Thanks!", line: "Thanks!", ask: "Just thank them, briefly and warmly."),
        QuickReply(key: "time", chip: "Ask for a time", line: "Could you send me a few times that work for you?",
                   ask: "Ask them to suggest a few times that suit them; don’t propose any yourself."),
    ]

    /// A person you could answer: not you, not a no-reply robot, not junk or a receipt.
    static func offered(to address: String?, mine: Set<String>, isJunk: Bool = false, bucket: String? = nil) -> Bool {
        let a = (address ?? "").lowercased()
        if a.isEmpty || SendGuard.isMine(a, mine) { return false }
        if a.range(of: #"^(no-?reply|do-?not-?reply|mailer-daemon|postmaster|notifications?|bounces?)\b"#,
                   options: .regularExpression) != nil { return false }
        return !(isJunk || (bucket ?? "").range(of: "^(receipts|automated|newsletters|spam|blocked)",
                                                options: .regularExpression) != nil)
    }
}

/// The server's tones, in the menu's order and words.
extension API.DraftTone {
    var label: String {
        switch self {
        case .brief: "Brief"
        case .balanced: "Balanced"
        case .warm: "Warm"
        case .formal: "Formal"
        }
    }
}

/// The server's rewrites, in the row's order and words.
extension API.RewriteOp {
    /// What a rewrite row offers, the composer's window's and a reply box's alike: the page's composer's row
    /// (`REWRITES`), the page's only one.
    static let offered: [API.RewriteOp] = [.shorten, .elaborate, .formal, .friendly, .fix, .translate]

    var label: String {
        switch self {
        case .shorten: "Shorten"
        case .elaborate: "Elaborate"
        case .formal: "Formal"
        case .friendly: "Friendly"
        case .fix: "Fix grammar"
        case .translate: "Translate…"
        }
    }
}

/// Snippets: a name typed with `;` before the cursor, and the {{placeholders}} what it brings in leaves to fill.
enum Snippets {
    /// A snippet's name as typed before the cursor (UTF-16 offsets, as the text view counts): a word that opens with
    /// `;`.
    static func wordBefore(_ body: String, caret: Int) -> (from: Int, word: String)? {
        let units = body as NSString
        guard caret > 0, caret <= units.length else { return nil }
        var from = caret
        while from > 0 {
            let unit = units.character(at: from - 1)
            if let scalar = Unicode.Scalar(unit), CharacterSet.whitespacesAndNewlines.contains(scalar) { break }
            from -= 1
        }
        guard from < caret, units.character(at: from) == UInt16(UInt8(ascii: ";")) else { return nil }
        return (from, units.substring(with: NSRange(location: from + 1, length: caret - from - 1)))
    }

    /// What that word brings in: the snippet of that name, or the only one it begins.
    static func offered(_ list: [API.Snippet], word: String) -> API.Snippet? {
        let typed = word.lowercased()
        let starting = typed.isEmpty ? [] : list.filter { $0.trigger.hasPrefix(typed) }
        return starting.first { $0.trigger == typed } ?? (starting.count == 1 ? starting[0] : nil)
    }

    private static let placeholder = try! NSRegularExpression(pattern: #"\{\{[^{}\n]{1,64}\}\}"#)

    /// Each {{placeholder}} a text holds, as UTF-16 ranges.
    static func placeholders(_ text: String) -> [NSRange] {
        placeholder.matches(in: text, range: NSRange(location: 0, length: (text as NSString).length)).map(\.range)
    }

    static func after(_ text: String, at: Int) -> NSRange? {
        placeholders(text).first { $0.location >= at }
    }

    static func before(_ text: String, at: Int) -> NSRange? {
        placeholders(text).last { $0.location + $0.length <= at }
    }
}

/// Pictures in the text: a mark that names a file by its token, and the file as the server takes it.
enum Pictures {
    static let types: Set<String> = ["image/png", "image/jpeg", "image/gif", "image/webp"]

    static func isPicture(_ type: String) -> Bool { types.contains(type.lowercased()) }

    /// A mark's name is the file's, less what would end the mark early.
    static func mark(_ name: String, cid: String) -> String {
        // By scalar: a CR LF is one Character, and either half ends the mark.
        let ending = "[]\r\n".unicodeScalars
        let cleaned = String(String(name.unicodeScalars.map { ending.contains($0) ? " " : Character($0) }).prefix(120))
        return "![\(cleaned)](cid:\(cid))"
    }

    static func withoutMark(_ body: String, cid: String) -> String {
        body.replacingOccurrences(of: #"!\[[^\[\]\n]*\]\(cid:\#(NSRegularExpression.escapedPattern(for: cid))\)"#,
                                  with: "", options: .regularExpression)
    }

    static func parts(_ files: [ComposeFile]) -> [API.PreviewPicture] {
        files.compactMap { file in
            guard let cid = file.cid, isPicture(file.contentType),
                  let type = API.PictureType(rawValue: file.contentType.lowercased()) else { return nil }
            return API.PreviewPicture(cid: cid, contentType: type)
        }
    }

    /// The server's preview names each picture as it would go out; the frame takes none of that, so each comes as the
    /// data this composer already holds.
    static func inline(_ html: String, files: [ComposeFile]) -> String {
        files.reduce(html) { out, file in
            guard let cid = file.cid else { return out }
            let pattern = #"src="cid:\#(NSRegularExpression.escapedPattern(for: cid))(?:@[^"]*)?""#
            return out.replacingOccurrences(of: pattern, with: "src=\"data:\(file.contentType);base64,\(file.base64)\"",
                                            options: .regularExpression)
        }
    }
}

/// When a scheduled send goes, in friendly local words.
enum SendTimes {
    static func words(_ epoch: Int, now: Date = Date(), calendar: Calendar = .current) -> String {
        let date = Date(timeIntervalSince1970: TimeInterval(epoch))
        let time = date.formatted(date: .omitted, time: .shortened)
        if calendar.isDate(date, inSameDayAs: now) { return "today \(time)" }
        if let tomorrow = calendar.date(byAdding: .day, value: 1, to: now), calendar.isDate(date, inSameDayAs: tomorrow) {
            return "tomorrow \(time)"
        }
        return "\(date.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())) \(time)"
    }

    /// The Later menu's times: in an hour, this evening, tomorrow at 8 and Monday at 8. This evening is offered only
    /// while 18:00 today is still ahead: once it has passed, the evening it would name is tomorrow's, which isn't this
    /// evening, and tomorrow at 8 comes first anyway.
    static func presets(now: Date = Date(), calendar: Calendar = .current) -> [(label: String, epoch: Int)] {
        let seconds = { (date: Date) in Int(date.timeIntervalSince1970) }
        let evening = calendar.date(bySettingHour: 18, minute: 0, second: 0, of: now).flatMap { $0 > now ? $0 : nil }
        let tomorrow = calendar.date(bySettingHour: 8, minute: 0, second: 0,
                                     of: calendar.date(byAdding: .day, value: 1, to: now) ?? now) ?? now
        let weekday = calendar.component(.weekday, from: now)   // 1 is Sunday
        let ahead = ((9 - weekday) % 7 == 0) ? 7 : (9 - weekday) % 7
        let monday = calendar.date(bySettingHour: 8, minute: 0, second: 0,
                                   of: calendar.date(byAdding: .day, value: ahead, to: now) ?? now) ?? now
        return [("In 1 hour", seconds(now) + 3600)] + (evening.map { [("This evening", seconds($0))] } ?? [])
            + [("Tomorrow 8 AM", seconds(tomorrow)), ("Monday 8 AM", seconds(monday))]
    }
}
#endif
