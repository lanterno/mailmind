#if os(macOS)
import Foundation

/// The command palette's match (lib/fuzzy.ts): each letter in order, closer together and at the start of a word
/// scoring higher. A shorter label wins a tie.
enum Fuzzy {
    static func score(_ needle: String, _ hay: String) -> Double? {
        let needle = Array(needle.lowercased()), hay = Array(hay.lowercased())
        guard !needle.isEmpty else { return 0 }
        var from = 0, score = 0.0, run = 0
        for character in needle {
            guard let found = hay[from...].firstIndex(of: character) else { return nil }
            run = found == from ? run + 1 : 0
            let atWord = found == 0 || " /·-".contains(hay[found - 1])
            score += Double(2 + run * 2 + (atWord ? 3 : 0) - (found - from))
            from = found + 1
        }
        return score - Double(hay.count) * 0.01
    }

    /// The entries that match `query`, best first; with no query, all of them as given, which keeps their groups.
    static func rank<T>(_ query: String, _ entries: [T], label: (T) -> String) -> [T] {
        let query = query.trimmingCharacters(in: .whitespaces)
        guard !query.isEmpty else { return entries }
        return entries.compactMap { entry in score(query, label(entry)).map { ($0, entry) } }
            .enumerated()
            .sorted { $0.element.0 != $1.element.0 ? $0.element.0 > $1.element.0 : $0.offset < $1.offset }
            .map(\.element.1)
    }
}
#endif
