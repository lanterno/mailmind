#if DEBUG && os(macOS)
import AppKit

/// Finding things as a person and VoiceOver find them (docs/test-architecture.md 5.1): by role and label, within a
/// region named by its accessibility identifier (tests/framework/anchors.toml) or by its own role and label, in the
/// app's windows front to back. The list's rows are found by what they show (`subject`, `sender`): a row is drawn, so
/// its parts are read from the cell that draws them, never from the app's models.
@MainActor
enum DriverFind {
    /// Something found, and what a scenario asks of it.
    @MainActor
    struct Match {
        let found: Found
        let window: NSWindow?

        var visible: Bool { DriverFind.visible(found, in: window) }

        var said: [String: Any] {
            let item = found.element as AnyObject
            var out: [String: Any] = [
                "role": found.role, "label": found.label, "identifier": found.identifier, "visible": visible,
                "enabled": (item.isAccessibilityEnabled?() ?? true),
                "focused": DriverFind.focused(found, in: window),
                "selected": (item.isAccessibilitySelected?() ?? false) || DriverFind.chosen(found),
            ]
            if let value = DriverFind.value(found.element) { out["value"] = value }
            out["text"] = DriverFind.text(found)
            let frame = found.frame
            out["frame"] = [frame.minX, frame.minY, frame.width, frame.height].map { Double($0) }
            return out
        }
    }

    /// The one element `spec` names; it throws saying what there was when none or several are.
    static func one(_ spec: [String: Any]) throws -> Match {
        let matches = try all(spec)
        let index = spec["index"] as? Int
        if let index {
            guard index < matches.count else { throw DriverCommands.Failed("no \(describe(spec)) number \(index + 1)") }
            return matches[index]
        }
        guard matches.count == 1, let match = matches.first else {
            throw DriverCommands.Failed(matches.isEmpty ? "nothing is \(describe(spec))"
                                                        : "\(matches.count) are \(describe(spec)): \(matches.map(\.found))")
        }
        return match
    }

    /// Every element `spec` names: `role` (an AX role, as "AXButton"), `label` (with `match`: "exact", "prefix",
    /// "contains" or "regex"), `within` (a region: {"id": …} or {"role": …, "label": …}), `where` (a row's `subject`
    /// or `sender`), `near` (within the region's part that says these words).
    static func all(_ spec: [String: Any]) throws -> [Match] {
        var found: [Match] = []
        for window in windows() {
            for root in try roots(spec["within"] as? [String: Any], in: window) {
                found += matches(spec, under: root, in: window)
            }
        }
        return found
    }

    /// The app's windows a person could see, front to back.
    static func windows() -> [NSWindow] {
        NSApp.orderedWindows.filter(\.isVisible)
    }

    private static func roots(_ region: [String: Any]?, in window: NSWindow) throws -> [Found] {
        guard let region else { return [Find.all(in: window).first].compactMap { $0 } }
        let everything = Find.all(in: window)
        if let id = region["id"] as? String {
            return everything.filter { $0.identifier == id && visible($0, in: window) }
        }
        let role = region["role"] as? String ?? ""
        let label = region["label"] as? String
        return everything.filter { $0.role == role && (label == nil || $0.label == label) && visible($0, in: window) }
    }

    private static func matches(_ spec: [String: Any], under root: Found, in window: NSWindow) -> [Match] {
        let role = spec["role"] as? String
        let where_ = spec["where"] as? [String: String] ?? [:]
        var under = Find.all(in: root.element)
        if let near = spec["near"] as? String {
            // The region's part (a line of the tray) that says `near`, and what is in it.
            let parts = (((root.element as AnyObject).accessibilityChildren?() ?? nil) ?? []).compactMap { $0 as? NSObject }
            under = parts.filter { part in Find.all(in: part).contains { $0.label.contains(near) } }
                .flatMap { Find.all(in: $0) }
        }
        if role == "row" || !where_.isEmpty {
            return rows(under: root, in: window).filter { cell in
                where_.allSatisfy { key, value in
                    switch key {
                    case "subject": cell.row?.subject == value
                    case "sender": cell.row?.sender == value
                    default: false
                    }
                }
            }.map { Match(found: Found(element: $0, role: "AXRow", label: $0.accessibilityLabel() ?? "",
                                       identifier: ""), window: window) }
        }
        return under.filter { item in
            // A role may name several, "AXTextField|AXTextArea", as a textbox on the page is either.
            (role == nil || role!.split(separator: "|").contains { $0 == item.role }) && labelMatches(item.label, spec)
        }.map { Match(found: $0, window: window) }
    }

    private static func labelMatches(_ label: String, _ spec: [String: Any]) -> Bool {
        guard let wanted = spec["label"] as? String else { return true }
        switch spec["match"] as? String ?? "exact" {
        case "prefix": return label.hasPrefix(wanted)
        case "contains": return label.contains(wanted)
        case "regex":
            guard let pattern = try? NSRegularExpression(pattern: wanted) else { return false }
            return pattern.firstMatch(in: label, range: NSRange(label.startIndex..., in: label)) != nil
        default: return label == wanted
        }
    }

    /// The list's rows under `root`, as their cells draw them, in order.
    static func rows(under root: Found, in window: NSWindow) -> [MailRowCell] {
        var tables: [NSTableView] = []
        func look(_ view: NSView) {
            if let table = view as? NSTableView { tables.append(table) }
            view.subviews.forEach(look)
        }
        if let view = root.element as? NSView { look(view) } else if let content = window.contentView {
            // A SwiftUI region isn't a view: its tables are those whose frame lies in its frame.
            look(content)
            let frame = root.frame
            tables = tables.filter { table in
                guard let scroll = table.enclosingScrollView else { return false }
                let onScreen = window.convertToScreen(scroll.convert(scroll.bounds, to: nil))
                return frame.isEmpty || frame.intersects(onScreen)
            }
        }
        return tables.flatMap { table in
            // Every row the list holds, as a person scrolling finds it: a row off the screen is made as the table
            // makes one, and is out of sight (`visible`) until a click scrolls it in.
            (0..<min(table.numberOfRows, 400)).compactMap {
                table.view(atColumn: 0, row: $0, makeIfNecessary: true) as? MailRowCell
            }
        }
    }

    /// On screen in its window: shown, with a size, inside the window and inside what scrolls it.
    static func visible(_ found: Found, in window: NSWindow?) -> Bool {
        guard let window, window.isVisible else { return false }
        let frame = found.frame
        guard frame.width >= 1, frame.height >= 1, window.frame.intersects(frame) else { return false }
        if let view = found.view {
            if view.isHiddenOrHasHiddenAncestor || view.alphaValue == 0 { return false }
            if view.window !== window { return false }
            let seen = view.visibleRect
            if seen.isEmpty { return false }
            let onScreen = window.convertToScreen(view.convert(seen, to: nil))
            if !onScreen.intersects(frame) { return false }
        }
        return true
    }

    /// Whether the keyboard is in it, or in something inside it.
    static func focused(_ found: Found, in window: NSWindow?) -> Bool {
        guard let window, let responder = window.firstResponder as? NSView else { return false }
        if let view = found.element as? NSView { return responder === view || responder.isDescendant(of: view) }
        let focused = (found.element as AnyObject).isAccessibilityFocused?() ?? false
        return focused
    }

    /// Whether it is a row of the list that is chosen.
    static func chosen(_ found: Found) -> Bool {
        (found.element as? MailRowCell)?.state.chosen ?? false
    }

    /// The words it shows: a field's text, else its label and value.
    static func text(_ found: Found) -> String {
        if let field = found.element as? NSTextField { return field.stringValue }
        if let text = found.element as? NSTextView { return text.string }
        let value = DriverFind.value(found.element) ?? ""
        return [found.label, value].filter { !$0.isEmpty }.joined(separator: " ")
    }

    private static func describe(_ spec: [String: Any]) -> String {
        var said = (spec["role"] as? String) ?? "anything"
        if let label = spec["label"] as? String { said += " “\(label)”" }
        for (key, value) in spec["where"] as? [String: String] ?? [:] { said += " \(key)=“\(value)”" }
        if let within = spec["within"] as? [String: Any] { said += " within \(within)" }
        return said
    }

    /// What an element's accessibility value says, as text.
    static func value(_ element: NSObject) -> String? {
        let raw: Any? = element.responds(to: #selector(NSAccessibilityProtocol.accessibilityValue))
            ? element.perform(#selector(NSAccessibilityProtocol.accessibilityValue))?.takeUnretainedValue() : nil
        if let text = raw as? String { return text }
        if let number = raw as? NSNumber { return number.stringValue }
        if let text = raw as? NSAttributedString { return text.string }
        return nil
    }

    /// One element of the tree, as `Find.all` reads each.
    static func node(_ object: NSObject) -> Found {
        let item = object as AnyObject
        let control = (object as? NSCell)?.controlView as AnyObject?
        let label = [(item.accessibilityLabel?() ?? nil), (item.accessibilityTitle?() ?? nil),
                     (control?.accessibilityLabel?() ?? nil), (item.accessibilityPlaceholderValue?() ?? nil)]
            .compactMap { $0 }.first { !$0.isEmpty } ?? ""
        return Found(element: object, role: (item.accessibilityRole?() ?? nil)?.rawValue ?? "", label: label,
                     identifier: item.accessibilityIdentifier?() ?? "")
    }

    /// The accessibility tree of the key window, or of a region in it, as text: for finding what a scenario can name.
    static func tree(_ region: String?) -> String {
        var out: [String] = []
        for window in windows() {
            out.append("window “\(window.title)” \(type(of: window)) key=\(window === NSApp.keyWindow)")
            func walk(_ object: Any, depth: Int) {
                guard let item = object as? NSObject, depth < 60 else { return }
                let me = DriverFind.node(item)
                let match = Match(found: me, window: window)
                if !me.role.isEmpty || !me.identifier.isEmpty {
                    let value = DriverFind.value(item).map { " = \($0)" } ?? ""
                    out.append(String(repeating: "  ", count: depth) + "\(me.role) “\(me.label)”"
                               + (me.identifier.isEmpty ? "" : " #\(me.identifier)") + value
                               + (match.visible ? "" : " (hidden)") + " \(type(of: item))")
                }
                for kid in ((item as AnyObject).accessibilityChildren?() ?? nil) ?? [] { walk(kid, depth: depth + 1) }
            }
            if let region {
                for root in Find.all(in: window) where root.identifier == region { walk(root.element, depth: 1) }
            } else {
                walk(window, depth: 1)
            }
        }
        return out.joined(separator: "\n")
    }
}
#endif
