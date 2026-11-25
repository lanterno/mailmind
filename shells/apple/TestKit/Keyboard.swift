import AppKit

/// Nil when the keyboard is somewhere a person can use it in `window`, else why it isn't. It is somewhere when the
/// window is visible and key, and its first responder is a view in it that is shown, has a size and can take keys:
/// a text view or field that can be edited or selected, or any other view that accepts first responder. The window
/// itself, a view hidden or of no size, one in a window that has closed, or no first responder at all, is nowhere:
/// keys typed then go nowhere a person can see.
///
/// An app that isn't active has no key window, and a test's app is never active: there the window need only be one
/// that can become key, as it would be once the app is in front. The hosted tests, in an active app, hold it to key.
@MainActor
func keyboardIsSomewhere(_ window: NSWindow) -> String? {
    guard window.isVisible else { return "the window isn't on screen" }
    if NSApp.isActive {
        guard window.isKeyWindow else { return "the window isn't key" }
    } else {
        guard window.canBecomeKey else { return "the window can't be key" }
    }
    guard let responder = window.firstResponder else { return "nothing is the first responder" }
    if responder === window { return "the first responder is the window itself" }
    guard let view = responder as? NSView else {
        return "the first responder is \(type(of: responder)), not a view"
    }
    guard view.window === window else { return "the first responder \(type(of: view)) isn't in the window" }
    if view.isHiddenOrHasHiddenAncestor { return "the first responder \(type(of: view)) is hidden" }
    if view.bounds.width < 1 || view.bounds.height < 1 {
        return "the first responder \(type(of: view)) has no size"
    }
    if view.visibleRect.isEmpty { return "the first responder \(type(of: view)) is out of sight" }
    if let text = view as? NSTextView {
        return text.isEditable || text.isSelectable ? nil : "the first responder is a text view that takes no keys"
    }
    if let field = view as? NSTextField {
        return field.isEditable || field.isSelectable ? nil : "the first responder is a label that takes no keys"
    }
    return view.acceptsFirstResponder ? nil : "the first responder \(type(of: view)) doesn't accept the keyboard"
}
