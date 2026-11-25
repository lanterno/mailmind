import AppKit

/// Something in a window as VoiceOver finds it: an element of the window's accessibility tree, a view of AppKit's or
/// one SwiftUI made, with its role, its label and where it stands.
@MainActor
struct Found {
    let element: NSObject
    let role: String
    let label: String
    let identifier: String

    /// Where it stands on the screen, as the accessibility tree says.
    var frame: CGRect { (element as AnyObject).accessibilityFrame?() ?? .zero }

    /// The view that holds it: itself where it is one, else the nearest view among its accessibility parents.
    var view: NSView? {
        var next: Any? = element
        while let object = next as? NSObject {
            if let view = object as? NSView { return view }
            if let cell = object as? NSCell, let view = cell.controlView { return view }
            next = (object as AnyObject).accessibilityParent?() ?? nil
        }
        return nil
    }

    /// Its middle in `view`'s coordinates, where a click on it lands.
    func middle(in view: NSView) throws -> CGPoint {
        guard let window = view.window else { throw KitError("the view is in no window") }
        let onScreen = frame
        guard !onScreen.isEmpty else { throw KitError("\(self) has no frame to click in") }
        let inWindow = window.convertPoint(fromScreen: CGPoint(x: onScreen.midX, y: onScreen.midY))
        return view.convert(inWindow, from: nil)
    }
}

extension Found: CustomStringConvertible {
    nonisolated var description: String {
        MainActor.assumeIsolated { "\(role) \"\(label)\"" + (identifier.isEmpty ? "" : " #\(identifier)") }
    }
}

/// Finding things in a window as a person and VoiceOver find them: by role and label, within a region a view's
/// identifier names, or by an identifier alone. SwiftUI builds its part of the tree only once an assistive app asks,
/// so a test of SwiftUI's views says it is one first (`Find.assistive(true)`).
@MainActor
enum Find {
    /// Every element under `root`, `root` first, in the tree's order.
    static func all(in root: Any) -> [Found] {
        guard let object = root as? NSObject else { return [] }
        let item = object as AnyObject
        let role = (item.accessibilityRole?() ?? nil)?.rawValue ?? ""
        // A control's cell stands for it in the tree, and its label is often the control's.
        let control = (object as? NSCell)?.controlView as AnyObject?
        let label = [(item.accessibilityLabel?() ?? nil), (item.accessibilityTitle?() ?? nil),
                     (control?.accessibilityLabel?() ?? nil), (item.accessibilityPlaceholderValue?() ?? nil)]
            .compactMap { $0 }.first { !$0.isEmpty } ?? ""
        let identifier = item.accessibilityIdentifier?() ?? ""
        let kids = (item.accessibilityChildren?() ?? nil) ?? []
        return [Found(element: object, role: role, label: label, identifier: identifier)]
            + kids.flatMap { all(in: $0) }
    }

    /// The one element with accessibility identifier `identifier` in `window`.
    static func identifier(_ identifier: String, in window: NSWindow) throws -> Found {
        try one(all(in: window).filter { $0.identifier == identifier }, "#\(identifier)", in: window)
    }

    /// The one element of `role` (an `NSAccessibility.Role`'s raw value, as "AXButton") labelled `label`, within the
    /// region whose identifier is `within` when one is named, in `window`.
    static func role(_ role: NSAccessibility.Role, _ label: String? = nil, within region: String? = nil,
                     in window: NSWindow) throws -> Found
    {
        let root: Any = try region.map { try identifier($0, in: window).element } ?? window
        let matches = all(in: root).filter { $0.role == role.rawValue && (label == nil || $0.label == label) }
        return try one(matches, "\(role.rawValue) \"\(label ?? "*")\"" + (region.map { " within #\($0)" } ?? ""),
                       in: window)
    }

    /// Tell the app an assistive app is (or no longer is) reading it, as VoiceOver does.
    static func assistive(_ on: Bool) {
        _ = NSApp.perform(NSSelectorFromString("accessibilitySetValue:forAttribute:"), with: NSNumber(value: on),
                          with: "AXEnhancedUserInterface")
    }

    private static func one(_ matches: [Found], _ what: String, in window: NSWindow) throws -> Found {
        guard matches.count == 1, let found = matches.first else {
            let seen = all(in: window).filter { !$0.role.isEmpty }.map(\.description).joined(separator: "\n")
            throw KitError(matches.isEmpty ? "nothing is \(what) in the window, which has:\n\(seen)"
                                           : "\(matches.count) are \(what): \(matches)")
        }
        return found
    }
}
