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
}
#endif
