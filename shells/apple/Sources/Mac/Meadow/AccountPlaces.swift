import Foundation

/// Which of the palette's colours each account wears: the place the core keeps with each account (the digest's
/// `colours`), so the page and the Mac colour an account the same and removing one moves no other's. Kept in this
/// Mac's preferences too, as its window's are, so what reads them before the digest comes (Settings, the compose
/// window) colours as the last digest said. A server that names no place falls back to places kept here: the accounts
/// as first seen take theirs in order, one added later the first place no account holds, and one removed gives its
/// place up.
enum AccountPlaces {
    static let key = "accountColourPlaces"

    /// The places once the accounts are read as `accounts`: the core's for each it names, the ones gone let go of, and
    /// each new one given the first place free, in the accounts' order.
    static func placed(_ accounts: [String], kept: [String: Int], core: [String: Int] = [:]) -> [String: Int] {
        var places = core.filter { accounts.contains($0.key) }
        for (account, place) in kept where accounts.contains(account) && places[account] == nil
            && !places.values.contains(place) {
            places[account] = place
        }
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

    /// The places once the accounts are read as `accounts`, with the core's `core`, kept in `defaults` where they
    /// changed.
    @discardableResult
    static func remember(_ accounts: [String], core: [String: Int] = [:],
                         in defaults: UserDefaults = .standard) -> [String: Int] {
        let kept = kept(in: defaults)
        let places = placed(accounts, kept: kept, core: core)
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
