#if os(macOS)
import Foundation

/// Paper Trail: a ledger read out of receipts' subjects and snippets, as the page reads it (discovery/model.ts). No AI
/// and no network, so it never needs either.
enum Ledger {
    struct Amount: Equatable {
        var currency: String
        var value: Double
    }

    struct Line: Identifiable {
        var hit: API.SearchHit
        var amount: Amount?
        var refund: Bool
        /// What the line adds to its month: a refund takes away.
        var shown: Double?
        var id: String { "\(hit.key):\(hit.uid):\(hit.folder)" }
    }

    struct Month: Identifiable {
        /// "2026-03", or "" for mail with no date.
        var yearMonth: String
        var lines: [Line]
        /// Each currency's subtotal, in the order the currencies came.
        var totals: [(currency: String, value: Double)]
        var id: String { yearMonth.isEmpty ? "undated" : yearMonth }
    }

    private static let money = try! NSRegularExpression(
        pattern: #"(?:[$€£]\s?\d[\d.,]*\d|\d[\d.,]*\d\s?(?:USD|EUR|GBP|EGP|CHF|AED))"#, options: [.caseInsensitive])
    private static let code = try! NSRegularExpression(pattern: "USD|EUR|GBP|EGP|CHF|AED", options: [.caseInsensitive])
    private static let refund = try! NSRegularExpression(
        pattern: #"\b(refund|credit note|reimburs|money back)\b"#, options: [.caseInsensitive])
    private static let symbols = ["USD": "$", "EUR": "€", "GBP": "£", "EGP": "E£", "CHF": "CHF", "AED": "AED"]
    private static let months = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                                 "October", "November", "December"]

    /// An amount as written, its currency and its value: a comma or a point may be either the decimals or the
    /// thousands, and the last of the two says which (`parseMoney`).
    static func parse(_ text: String) -> (currency: String, value: Double?) {
        var currency = ""
        if let symbol = text.first(where: { "$€£".contains($0) }) {
            currency = String(symbol)
        } else if let found = code.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
                  let range = Range(found.range, in: text) {
            currency = symbols[text[range].uppercased()] ?? ""
        }
        var number = text.filter { $0.isASCII && ($0.isNumber || $0 == "." || $0 == ",") }
        if number.contains(","), number.contains(".") {
            if let comma = number.lastIndex(of: ","), let point = number.lastIndex(of: "."), comma > point {
                number = number.replacingOccurrences(of: ".", with: "")
                if let first = number.firstIndex(of: ",") { number.replaceSubrange(first...first, with: ".") }
            } else {
                number = number.replacingOccurrences(of: ",", with: "")
            }
        } else if number.contains(",") {
            if number.range(of: #",\d{2}$"#, options: .regularExpression) != nil, let first = number.firstIndex(of: ",") {
                number.replaceSubrange(first...first, with: ".")
            } else {
                number = number.replacingOccurrences(of: ",", with: "")
            }
        }
        return (currency, leadingNumber(number))
    }

    /// What JavaScript's parseFloat reads of the text: the number at its start, if any.
    private static func leadingNumber(_ text: String) -> Double? {
        guard let range = text.range(of: #"^\d+(\.\d*)?"#, options: .regularExpression) else { return nil }
        return Double(text[range])
    }

    /// The most prominent amount: receipts repeat the total, so the largest (`extractAmount`).
    static func amount(subject: String, snippet: String) -> Amount? {
        let text = String("\(subject)  \(snippet)".prefix(400))
        var best: Amount?
        for found in money.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
            guard let range = Range(found.range, in: text) else { continue }
            let (currency, value) = parse(String(text[range]))
            if let value, value > (best?.value ?? -.infinity) { best = Amount(currency: currency, value: value) }
        }
        return best
    }

    static func isRefund(_ subject: String) -> Bool {
        refund.firstMatch(in: subject, range: NSRange(subject.startIndex..., in: subject)) != nil
    }

    /// An amount with its currency, a negative with a real minus (`fmtMoney`).
    static func money(_ currency: String, _ value: Double, locale: Locale = .current) -> String {
        let format = NumberFormatter()
        format.locale = locale
        format.numberStyle = .decimal
        format.minimumFractionDigits = 2
        format.maximumFractionDigits = 2
        let digits = format.string(from: NSNumber(value: abs(value))) ?? String(format: "%.2f", abs(value))
        return (value < 0 ? "−" : "") + currency + digits
    }

    /// "March 2026", or "Undated" (`monthName`).
    static func monthName(_ yearMonth: String) -> String {
        let parts = yearMonth.split(separator: "-")
        guard yearMonth.range(of: #"^\d{4}-\d{2}$"#, options: .regularExpression) != nil, let month = Int(parts[1]),
              (1...12).contains(month) else { return String(localized: "Undated") }
        return "\(months[month - 1]) \(parts[0])"
    }

    /// The hits by month as they come, newest first, with a subtotal for each currency (`ledger`).
    static func months(_ hits: [API.SearchHit]) -> [Month] {
        var months: [Month] = []
        for hit in hits {
            let yearMonth = String(hit.date.prefix(7))
            if months.last?.yearMonth != yearMonth { months.append(Month(yearMonth: yearMonth, lines: [], totals: [])) }
            let amount = Self.amount(subject: hit.subject, snippet: hit.snippet)
            let refund = amount != nil && isRefund(hit.subject)
            let shown = amount.map { refund ? -$0.value : $0.value }
            var month = months.removeLast()
            if let amount, let shown {
                if let at = month.totals.firstIndex(where: { $0.currency == amount.currency }) {
                    month.totals[at].value += shown
                } else {
                    month.totals.append((amount.currency, shown))
                }
            }
            month.lines.append(Line(hit: hit, amount: amount, refund: refund, shown: shown))
            months.append(month)
        }
        return months
    }

    // MARK: A month's totals

    /// What a month's card says (`.money`, `.money-sub`): the total in its first currency, how many receipts and
    /// refunds, and the month before's total in the same currency, when the trail has one.
    struct Summary: Equatable {
        var yearMonth: String
        var currency: String
        var total: Double
        var receipts: Int
        var refunds: Int
        var before: Double?
        var beforeMonth: String?
    }

    /// The summary of `months[index]`; none for a month with no amount in it.
    static func summary(_ months: [Month], at index: Int) -> Summary? {
        guard months.indices.contains(index), let first = months[index].totals.first else { return nil }
        let month = months[index]
        let previous = months.indices.contains(index + 1) ? months[index + 1] : nil
        let before = previous.flatMap { $0.yearMonth.isEmpty ? nil : $0 }?.totals
            .first { $0.currency == first.currency }?.value
        return Summary(yearMonth: month.yearMonth, currency: first.currency, total: first.value,
                       receipts: month.lines.count { $0.amount != nil && !$0.refund },
                       refunds: month.lines.count(where: \.refund), before: before,
                       beforeMonth: before == nil ? nil : previous?.yearMonth)
    }

    /// "4 receipts · 1 refund · $41.00 less than September".
    static func words(_ summary: Summary, locale: Locale = .current) -> String {
        var parts = [countNoun(summary.receipts, "receipt", "receipts")]
        if summary.refunds > 0 { parts.append(countNoun(summary.refunds, "refund", "refunds")) }
        if let before = summary.before, let month = summary.beforeMonth {
            let difference = summary.total - before
            let name = monthOnly(month)
            if abs(difference) < 0.005 {
                parts.append(String(localized: "the same as \(name)"))
            } else {
                let amount = money(summary.currency, abs(difference), locale: locale)
                parts.append(difference < 0 ? String(localized: "\(amount) less than \(name)")
                    : String(localized: "\(amount) more than \(name)"))
            }
        }
        return parts.joined(separator: " · ")
    }

    /// "October", for a month the card names without its year; "Undated" for mail with no date.
    static func monthOnly(_ yearMonth: String) -> String {
        let name = monthName(yearMonth)
        return name.split(separator: " ").first.map(String.init) ?? name
    }

    /// The bars under a month's total (`.bars`): up to `count` months ending at `months[index]`, oldest first, each
    /// its total in the summary's currency over the largest of them (from 0 to 1), the last the month itself.
    static func bars(_ months: [Month], at index: Int, count: Int = 6) -> [Double] {
        guard let summary = summary(months, at: index) else { return [] }
        let dated = months.enumerated().filter { !$0.element.yearMonth.isEmpty && $0.offset >= index }.prefix(count)
        let values = dated.map { _, month in
            max(0, month.totals.first { $0.currency == summary.currency }?.value ?? 0)
        }.reversed()
        let most = values.max() ?? 0
        return values.map { most > 0 ? $0 / most : 0 }
    }

    /// Each account's total in a month's currencies, the largest first (`By account`).
    static func byAccount(_ month: Month) -> [(account: String, currency: String, value: Double)] {
        var totals: [(account: String, currency: String, value: Double)] = []
        for line in month.lines {
            guard let amount = line.amount, let shown = line.shown else { continue }
            if let at = totals.firstIndex(where: { $0.account == line.hit.account && $0.currency == amount.currency }) {
                totals[at].value += shown
            } else {
                totals.append((line.hit.account, amount.currency, shown))
            }
        }
        return totals.enumerated().sorted { a, b in
            a.element.value != b.element.value ? a.element.value > b.element.value : a.offset < b.offset
        }.map(\.element)
    }

    /// How many of a month's lines sit in each folder, the fullest first (`Filed under`).
    static func byFolder(_ month: Month) -> [(folder: String, count: Int)] {
        var counts: [(folder: String, count: Int)] = []
        for line in month.lines {
            if let at = counts.firstIndex(where: { $0.folder == line.hit.folder }) {
                counts[at].count += 1
            } else {
                counts.append((line.hit.folder, 1))
            }
        }
        return counts.enumerated().sorted { a, b in
            a.element.count != b.element.count ? a.element.count > b.element.count : a.offset < b.offset
        }.map(\.element)
    }
}
#endif
