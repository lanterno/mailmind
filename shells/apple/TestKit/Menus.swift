import AppKit

/// An item of the menu bar as AppKit validates it before it is shown or its key is matched.
struct MenuItemState: Equatable, Sendable {
    let title: String
    let enabled: Bool
    let checked: Bool
}

/// The item at `path` in `bar` (the app's menu bar unless a test names another), as the menu bar shows it once AppKit
/// has validated it: each menu on the way asked to update, as it is before it opens, so each item's target is found
/// and asked `validateMenuItem(_:)`. It throws where no item is at `path`.
///
/// An item with no target of its own goes to the key window's chain of responders. An app that isn't active has no
/// key window, and a test's app is never active: there a test names the window that would be key as `window`, and
/// the kit looks through its chain as AppKit would through the key window's.
@MainActor
func menuItem(_ path: [String], in bar: NSMenu? = NSApp.mainMenu, for window: NSWindow? = nil) throws
    -> MenuItemState
{
    let (item, _) = try validatedItem(path, in: bar, for: window)
    return MenuItemState(title: item.title, enabled: item.isEnabled, checked: item.state == .on)
}

/// The item at `path` chosen as a person chooses it from the menu bar, its action sent to the target it validated
/// against. It throws where the item is disabled, as a person couldn't choose it then.
@MainActor
func chooseMenuItem(_ path: [String], in bar: NSMenu? = NSApp.mainMenu, for window: NSWindow? = nil) throws {
    let (item, target) = try validatedItem(path, in: bar, for: window)
    let named = path.joined(separator: " › ")
    guard item.isEnabled else { throw KitError("\(named) is disabled") }
    guard let action = item.action else { throw KitError("\(named) does nothing") }
    guard NSApp.sendAction(action, to: target, from: item) else { throw KitError("nothing took \(named)") }
}

/// The item at `path`, validated, and the target its action goes to.
@MainActor
private func validatedItem(_ path: [String], in bar: NSMenu?, for window: NSWindow?) throws
    -> (NSMenuItem, AnyObject?)
{
    guard var menu = bar else { throw KitError("the app has no menu bar") }
    guard !path.isEmpty else { throw KitError("an empty menu path") }
    for (depth, title) in path.enumerated() {
        menu.delegate?.menuNeedsUpdate?(menu)
        menu.update()
        guard let item = menu.items.first(where: { $0.title == title }) else {
            let titles = menu.items.map(\.title).filter { !$0.isEmpty }.joined(separator: ", ")
            throw KitError("no \(title) in \(depth == 0 ? "the menu bar" : path[depth - 1]): \(titles)")
        }
        if depth < path.count - 1 {
            guard let submenu = item.submenu else { throw KitError("\(title) opens no menu") }
            menu = submenu
            continue
        }
        guard let action = item.action else { return (item, item.target) }
        let chain: NSWindow? = NSApp.keyWindow == nil ? window : nil
        let target: AnyObject? = item.target ?? chain.flatMap { responder(for: action, through: $0) }
            ?? NSApp.target(forAction: action, to: nil, from: item) as AnyObject?
        if item.target == nil, chain != nil, menu.autoenablesItems {
            item.isEnabled = target.map { validate(item, by: $0) } ?? false
        }
        return (item, target)
    }
    throw KitError("an empty menu path")
}

/// The first of `window`'s responders, then the window, its delegate, the app and its delegate, that answers
/// `action`: where AppKit sends an action with no target.
@MainActor
private func responder(for action: Selector, through window: NSWindow) -> AnyObject? {
    var chain: [AnyObject] = []
    var next: NSResponder? = window.firstResponder
    while let responder = next {
        chain.append(responder)
        next = responder.nextResponder
    }
    if !chain.contains(where: { $0 === window }) { chain.append(window) }
    if let delegate = window.delegate { chain.append(delegate) }
    chain.append(NSApp)
    if let delegate = NSApp.delegate { chain.append(delegate) }
    return chain.first { $0.responds(to: action) }
}

/// What `target` says of `item`, as AppKit asks it: an item validator first, then a user interface one, else yes.
@MainActor
private func validate(_ item: NSMenuItem, by target: AnyObject) -> Bool {
    if let judge = target as? NSMenuItemValidation { return judge.validateMenuItem(item) }
    if let judge = target as? NSUserInterfaceValidations { return judge.validateUserInterfaceItem(item) }
    return true
}
