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

    /// What was typed, split on commas and semicolons.
    static func split(_ raw: String) -> [String] {
        raw.split(whereSeparator: { $0 == "," || $0 == ";" })
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
    }

    /// Where a Reply-To sends a reply instead of to the mail's sender, or nil if it sends it to them.
    static func redirect(replyTo: String?, sender: String?) -> Redirect? {
        let to = first(replyTo).lowercased(), from = (sender ?? "").lowercased()
        return !to.isEmpty && !from.isEmpty && to != from ? Redirect(to: to, sender: from) : nil
    }

    /// A subject with a reply's or forward's prefix, once.
    static func prefixed(_ subject: String?, _ prefix: String) -> String {
        let trimmed = (subject ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.range(of: #"^(re|fwd?|fw)\s*:"#, options: [.regularExpression, .caseInsensitive]) != nil {
            let rest = trimmed.replacingOccurrences(of: #"^(re|fwd?|fw)\s*:\s*"#, with: "",
                                                    options: [.regularExpression, .caseInsensitive])
            return "\(prefix) \(rest)"
        }
        return trimmed.isEmpty ? prefix : "\(prefix) \(trimmed)"
    }
}

/// The seeds a reply and a forward open with.
enum Seeds {
    private static func quote(_ d: API.MessageOpened) -> Quote {
        Quote(html: d.hasHtml ? d.html : nil, text: d.text ?? "",
              from: [d.fromName, d.fromAddr].compactMap { $0 }.first { !$0.isEmpty } ?? "", date: d.date)
    }

    static func reply(_ d: API.MessageOpened, all: Bool, mine: Set<String>) -> ComposeSeed {
        let toAddress = [Addresses.first(d.replyTo), d.fromAddr ?? ""].first { !$0.isEmpty } ?? ""
        var cc: [String] = []
        if all {
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
        seed.to = toAddress.isEmpty ? [] : [toAddress]
        seed.cc = cc
        seed.autoCc = cc
        // Not Reply-To: a redirect to a stranger gets marked.
        seed.participants = [d.fromAddr ?? ""] + Addresses.parse(d.to) + Addresses.parse(d.cc)
        seed.redirect = Addresses.redirect(replyTo: d.replyTo, sender: d.fromAddr)
        seed.subject = Addresses.prefixed(d.subject, "Re:")
        seed.quote = quote(d)
        seed.origKey = d.key
        return seed
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

    /// The Later menu's times: in an hour, this evening, tomorrow at 8 and Monday at 8.
    static func presets(now: Date = Date(), calendar: Calendar = .current) -> [(label: String, epoch: Int)] {
        let seconds = { (date: Date) in Int(date.timeIntervalSince1970) }
        var evening = calendar.date(bySettingHour: 18, minute: 0, second: 0, of: now) ?? now
        if evening <= now { evening = calendar.date(byAdding: .day, value: 1, to: evening) ?? evening }
        let tomorrow = calendar.date(bySettingHour: 8, minute: 0, second: 0,
                                     of: calendar.date(byAdding: .day, value: 1, to: now) ?? now) ?? now
        let weekday = calendar.component(.weekday, from: now)   // 1 is Sunday
        let ahead = ((9 - weekday) % 7 == 0) ? 7 : (9 - weekday) % 7
        let monday = calendar.date(bySettingHour: 8, minute: 0, second: 0,
                                   of: calendar.date(byAdding: .day, value: ahead, to: now) ?? now) ?? now
        return [("In 1 hour", seconds(now) + 3600), ("This evening", seconds(evening)), ("Tomorrow 8 AM", seconds(tomorrow)),
                ("Monday 8 AM", seconds(monday))]
    }
}
#endif
