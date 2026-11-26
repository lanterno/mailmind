#if DEBUG && os(macOS)
import AppKit

/// What a person sees, read from the accessibility tree in the shape the page's reading has
/// (tests/framework/mmtest/shown.py, `Shown.read`): the regions by their identifiers (tests/framework/anchors.toml) or
/// by role and label, and within them what VoiceOver says, visible only. The list's rows are read from the cells that
/// draw them, as their VoiceOver label is made, never from the app's models.
@MainActor
enum DriverShown {
    static func read() -> [String: Any] {
        guard let window = DevDriver.keyWindow() else { return ["title": "", "path": ""] }
        let main = mainWindow(from: window)
        var out: [String: Any] = ["title": main.title, "path": ""]
        let everything = Find.all(in: main)
        let seen = { (found: Found) in DriverFind.visible(found, in: main) }
        if let rail = everything.first(where: { $0.role == "AXGroup" && $0.label == "Mailboxes" && seen($0) }) {
            out["rail"] = self.rail(rail, in: main)
        }
        if let list = everything.first(where: { $0.identifier == "list" && seen($0) }) {
            out["list"] = self.list(list, in: main)
        }
        if let reader = everything.first(where: { $0.identifier == "reader" && seen($0) }) {
            out["reader"] = self.reader(reader, in: main)
        }
        out["composers"] = composers()
        out["dialogs"] = dialogs(over: main, everything: everything)
        let (floats, toast) = toasts()
        out["floats"] = floats
        out["toast"] = toast
        out["tray"] = tray()
        out["focus"] = focus() as Any
        return out
    }

    /// The mail window under a sheet or a dialog window: the window a sheet hangs on, else the key window.
    private static func mainWindow(from window: NSWindow) -> NSWindow {
        window.sheetParent ?? window
    }

    // MARK: The regions

    private static func rail(_ rail: Found, in window: NSWindow) -> [String: Any] {
        let under = Find.all(in: rail.element).filter { DriverFind.visible($0, in: window) }
        let account = under.first { $0.role == "AXButton" && $0.label.hasPrefix("Account: ") }
            .map { String($0.label.dropFirst("Account: ".count)) } ?? ""
        let places = under.filter { $0.role == "AXButton" && !$0.label.hasPrefix("Account: ") }.map { place in
            let value = DriverFind.value(place.element) ?? ""
            return ["name": [words(place.label), value].filter { !$0.isEmpty }.joined(separator: " "),
                    "current": selected(place)] as [String: Any]
        }
        return ["account": account, "places": places]
    }

    private static func list(_ list: Found, in window: NSWindow) -> [String: Any] {
        let under = Find.all(in: list.element).filter { DriverFind.visible($0, in: window) }
        let title = under.first { $0.role == "AXHeading" }?.label ?? ""
        let tabs = Find.all(in: window).filter { $0.identifier == "tabs" && DriverFind.visible($0, in: window) }
            .flatMap { Find.all(in: $0.element) }
            .filter { $0.role == "AXButton" && DriverFind.visible($0, in: window) }
            .map { ["name": words($0.label), "selected": selected($0)] as [String: Any] }
        let cells = DriverFind.rows(under: list, in: window).filter { $0.row != nil }
        let rows = cells.map { row($0, in: window) }
        var empty = ""
        if rows.isEmpty {
            empty = under.filter { $0.role == "AXStaticText" || ($0.role == "AXHeading" && $0.label != title) }
                .map { said($0) }.filter { !$0.isEmpty }.joined(separator: " ")
        }
        return ["title": title, "tabs": tabs, "rows": rows, "empty": empty]
    }

    private static func row(_ cell: MailRowCell, in window: NSWindow) -> [String: Any] {
        guard let row = cell.row else { return [:] }
        let found = Found(element: cell, role: "AXRow", label: cell.accessibilityLabel() ?? "", identifier: "")
        return [
            "sender": row.sender, "subject": row.subject, "when": row.when,
            "tags": RowWords.chips(row).map(\.text).joined(separator: " "),
            "unread": !row.seen, "chosen": cell.state.chosen, "marked": cell.state.checked,
            "visible": DriverFind.visible(found, in: window),
        ]
    }

    private static func reader(_ reader: Found, in window: NSWindow) -> [String: Any] {
        let under = Find.all(in: reader.element).filter { DriverFind.visible($0, in: window) }
        if let from = under.first(where: { $0.role == "AXGroup" && $0.label.hasPrefix("Message from ") }) {
            let subject = under.first { $0.role == "AXHeading" && DriverFind.value($0.element) == $0.label }?.label
                ?? under.first { $0.role == "AXHeading" }?.label ?? ""
            let thread = under.first { $0.role == "AXStaticText" && (DriverFind.value($0.element) ?? "")
                .range(of: #"^\d+ messages?$"#, options: .regularExpression) != nil }
            return ["kind": "message", "subject": subject, "sender": String(from.label.dropFirst("Message from ".count)),
                    "thread": thread.map { said($0) } ?? ""]
        }
        let brief = under.contains { $0.label == "Today’s brief" }
        let words = under.filter { ["AXHeading", "AXStaticText"].contains($0.role) }.map { said($0) }
            .filter { !$0.isEmpty }
        return ["kind": brief ? "brief" : "empty", "said": words.joined(separator: brief ? " · " : " ")]
    }

    /// Each composer on screen: a compose window's, or a reply's in the reader (identifier "composer").
    private static func composers() -> [[String: Any]] {
        var out: [[String: Any]] = []
        for window in DriverFind.windows() {
            let everything = Find.all(in: window)
            let mailWindow = everything.contains { $0.identifier == "reader" }
            for composer in everything where composer.identifier == "composer"
                && DriverFind.visible(composer, in: window) {
                let under = Find.all(in: composer.element)
                let field = { (label: String) in under.first { $0.label == label } }
                // A field's recipients are its chips, each with its Remove button, as the page's are, in the part
                // that holds the field.
                let chips = { (label: String) -> [String] in
                    for part in under where part.role == "AXScrollArea" || part.role == "AXGroup" {
                        let inside = Find.all(in: part.element)
                        guard inside.contains(where: { $0.label == label && $0.role.hasPrefix("AXText") }) else { continue }
                        return inside.filter { $0.role == "AXButton" && $0.label.hasPrefix("Remove ") }
                            .map { String($0.label.dropFirst("Remove ".count)) }
                    }
                    return []
                }
                let asks = under.filter { $0.role == "AXGroup" && $0.label == "Not sent yet" }.map { group in
                    let parts = Find.all(in: group.element).filter { DriverFind.visible($0, in: window) }
                    return ([group.label] + parts.filter { $0.role == "AXStaticText" }.map { said($0) }
                            + parts.filter { $0.role == "AXButton" }.map { "[\($0.label)]" })
                        .joined(separator: " · ")
                }
                // A compose window is titled as the page's composer is ("Reply"); a reply in the reader says whom
                // it answers ("Reply to Lin"), and names them on a line of its own ("To Lin Mori").
                let inline = mailWindow
                let toLine = under.lazy.compactMap { $0.role == "AXStaticText" ? DriverFind.value($0.element) : nil }
                    .first { $0.hasPrefix("To ") }.map { String($0.dropFirst(3)) }
                out.append([
                    "title": inline ? composer.label : window.title,
                    "to": inline ? toLine.map { [$0] } ?? [] : chips("To"), "cc": chips("Cc"), "bcc": chips("Bcc"),
                    "subject": field("Subject").map { value($0) } ?? "",
                    "body": field("message body").map { value($0) } ?? "",
                    "asks": asks,
                ])
            }
        }
        return out
    }

    /// The sheets and dialogs over the window: a sheet's window, or a dialog on the window's scrim.
    private static func dialogs(over main: NSWindow, everything: [Found]) -> [[String: Any]] {
        var out: [[String: Any]] = []
        var sheets: [NSWindow] = []
        if let sheet = main.attachedSheet { sheets.append(sheet) }
        sheets += DriverFind.windows().filter { $0 !== main && $0.isSheet && !sheets.contains($0) }
        for sheet in sheets where sheet.isVisible {
            let under = Find.all(in: sheet).filter { DriverFind.visible($0, in: sheet) }
            out.append(dialog(name: sheet.title, under: under))
        }
        return out
    }

    private static func dialog(name: String, under: [Found]) -> [String: Any] {
        ["name": name,
         "heading": under.first { $0.role == "AXHeading" }.map { said($0) } ?? "",
         "buttons": under.filter { $0.role == "AXButton" && !$0.label.isEmpty }.map(\.label)]
    }

    /// The toasts at the window's foot: those that offer something (Undo, a send's Cancel) as the page's floats, the
    /// rest as its toast.
    private static func toasts() -> ([String], String) {
        var floats: [String] = [], toast: [String] = []
        for window in DriverFind.windows() {
            for found in Find.all(in: window) where found.identifier == "toast" && DriverFind.visible(found, in: window) {
                let parts = Find.all(in: found.element).filter { DriverFind.visible($0, in: window) }
                let words = parts.filter { $0.role == "AXStaticText" }.map { said($0) }.filter { !$0.isEmpty }
                let buttons = parts.filter { $0.role == "AXButton" }.map(\.label).filter { !$0.isEmpty }
                let line = (words + buttons).joined(separator: " ")
                if buttons.isEmpty { toast.append(line) } else { floats.append(line) }
            }
        }
        return (floats, toast.joined(separator: " "))
    }

    /// The outbox's tray, where it is open: a line each.
    private static func tray() -> [String]? {
        for window in DriverFind.windows() {
            for found in Find.all(in: window) where found.identifier == "tray" && DriverFind.visible(found, in: window) {
                let kids = ((found.element as AnyObject).accessibilityChildren?() ?? nil) ?? []
                return kids.compactMap { $0 as? NSObject }.map { kid in
                    Find.all(in: kid).filter { DriverFind.visible($0, in: window) }.map { said($0) }
                        .filter { !$0.isEmpty }.joined(separator: " ")
                }.filter { !$0.isEmpty }
            }
        }
        return nil
    }

    // MARK: The keyboard

    /// Where the keyboard is: the region, the role and the label of what has it, or nil where nothing does.
    static func focus() -> [String: Any]? {
        guard let window = DevDriver.keyWindow(), let responder = window.firstResponder,
              responder !== window else { return nil }
        var element: NSObject = responder
        // A field being edited has the window's field editor as its responder: the field is what has the keyboard.
        if let editor = responder as? NSTextView, editor.isFieldEditor, let field = editor.delegate as? NSTextField {
            element = field
        }
        // SwiftUI's focus is inside its hosting view: what VoiceOver says has it.
        if String(describing: type(of: element)).contains("Hosting"),
           let inner = (element as AnyObject).accessibilityFocusedUIElement as? NSObject, inner !== element {
            element = inner
        }
        let found = DriverFind.node(element)
        if element is NSView, found.role.isEmpty || found.role == "AXUnknown", found.label.isEmpty {
            // A view of the window's own that takes keys for it (the frame, a catcher of single keys).
            return ["region": region(of: element, in: window), "role": "window", "label": "",
                    "visible": window.isVisible]
        }
        let typed = element is NSTextField || element is NSTextView
        let shown = (element as? NSView).map { !$0.isHiddenOrHasHiddenAncestor && $0.window != nil } ?? false
        return ["region": region(of: element, in: window), "role": typed ? "textbox" : role(found.role),
                "label": found.label, "visible": DriverFind.visible(found, in: window) || shown]
    }

    private static let regions: Set<String> = ["list", "reader", "composer", "toast", "tray", "top bar"]

    /// The region something is in: the nearest region's identifier above it, a sheet's dialog, or the rail.
    private static func region(of element: NSObject, in window: NSWindow) -> String {
        if window.isSheet || window.sheetParent != nil { return window.title.isEmpty ? "dialog" : "dialog \"\(window.title)\"" }
        var at: AnyObject? = element
        var steps = 0
        while let now = at, steps < 80 {
            if let id = now.accessibilityIdentifier?(), regions.contains(id) { return id }
            if now.accessibilityRole?() == .group, now.accessibilityLabel?() == "Mailboxes" { return "rail" }
            if let view = now as? NSTableView, view is MailboxTableView { return "list" }
            at = now.accessibilityParent?() as AnyObject?
            steps += 1
        }
        return "window"
    }

    /// An AX role as the page names roles: AXButton is a button, AXTextArea a textbox.
    private static func role(_ ax: String) -> String {
        switch ax {
        case "AXButton": "button"
        case "AXTextField", "AXTextArea": "textbox"
        case "AXComboBox": "combobox"
        case "AXTable", "AXOutline", "AXList": "listbox"
        case "AXRow", "AXCell": "option"
        case "AXCheckBox": "checkbox"
        case "AXRadioButton": "radio"
        case "AXLink": "link"
        case "AXGroup": "group"
        case "AXWindow", "": "window"
        default: String(ax.dropFirst(2)).lowercased()
        }
    }

    // MARK: Words

    /// What it says: its label, else its value.
    private static func said(_ found: Found) -> String {
        (found.label.isEmpty ? (DriverFind.value(found.element) ?? "") : found.label)
            .split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }

    private static func value(_ found: Found) -> String {
        if let field = found.element as? NSTextField { return field.stringValue }
        if let text = found.element as? NSTextView { return text.string }
        return DriverFind.value(found.element) ?? ""
    }

    /// A label as the page's text reads: VoiceOver's commas between its parts dropped ("Needs you, 0" is "Needs you 0").
    private static func words(_ label: String) -> String {
        label.replacingOccurrences(of: ", ", with: " ").split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }

    private static func selected(_ found: Found) -> Bool {
        (found.element as AnyObject).isAccessibilitySelected?() ?? false
    }
}
#endif
