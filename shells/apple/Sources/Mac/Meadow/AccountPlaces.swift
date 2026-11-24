import Foundation

/// Which of the palette's colours each account wears: a place kept for it. The accounts as first seen take their places
/// in order, as the page colours them by their order; one added later takes the first place no account holds, and one
/// removed gives its place up, so removing an account leaves every other account's colour as it was. The core keeps no
/// colour and the accounts no number, so the places are kept in this Mac's preferences, as its window's are.
enum AccountPlaces {
    static let key = "accountColourPlaces"

    /// The places once the accounts are read as `accounts`: the ones gone let go of, each new one given the first place
    /// free, in the accounts' order.
    static func placed(_ accounts: [String], kept: [String: Int]) -> [String: Int] {
        var places = kept.filter { accounts.contains($0.key) }
        for account in accounts where places[account] == nil {
            let taken = Set(places.values)
            places[account] = (0...).first { !taken.contains($0) } ?? 0
        }
        return places
    }

    /// The places kept in `defaults`.
    static func kept(in defaults: UserDefaults = .standard) -> [String: Int] {
        (defaults.dictionary(forKey: key) as? [String: Int]) ?? [:]
    }

    /// The places once the accounts are read as `accounts`, kept in `defaults` where they changed.
    @discardableResult
    static func remember(_ accounts: [String], in defaults: UserDefaults = .standard) -> [String: Int] {
        let kept = kept(in: defaults)
        let places = placed(accounts, kept: kept)
        if places != kept { defaults.set(places, forKey: key) }
        return places
    }

    /// The place of `account`'s colour: the one kept for it, else its place among `accounts`, as before any was kept.
    static func place(of account: String, among accounts: [String], in places: [String: Int]) -> Int {
        places[account] ?? accounts.firstIndex(of: account) ?? 0
    }

    /// The same, by the places kept in `defaults`.
    static func place(of account: String, among accounts: [String], in defaults: UserDefaults = .standard) -> Int {
        place(of: account, among: accounts, in: kept(in: defaults))
    }
}
