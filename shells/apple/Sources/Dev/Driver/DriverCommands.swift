#if DEBUG && os(macOS)
import AppKit
import Carbon.HIToolbox

/// What the driver does for each of the runner's commands (docs/test-architecture.md section 5.2). Input goes through
/// `MailMindTestKit` (`RealKeys`, `RealMouse`, `chooseMenuItem`), into the window the driver made key; reading goes
/// through the accessibility tree (`DriverFind`, `DriverShown`); `settle` is section 7's (`DriverSettle`); `core` is
/// the core's test world (`CoreHost.test_*`) and its routes.
@MainActor
enum DriverCommands {
    struct Failed: Error, CustomStringConvertible {
        let description: String
        init(_ description: String) { self.description = description }
    }

    static func run(_ command: [String: Any]) async -> [String: Any] {
        do {
            var out = try await carry(command)
            out["ok"] = true
            let refused = DevDriver.takeRefusals()
            if !refused.isEmpty { out["refused"] = refused }
            let seen = LaneA.anyOnScreen
            if !seen.isEmpty { out["on_screen"] = seen }
            return out
        } catch {
            return ["ok": false, "error": String(describing: error)]
        }
    }

    private static func carry(_ c: [String: Any]) async throws -> [String: Any] {
        let verb = c["do"] as? String ?? ""
        switch verb {
        case "ping":
            return [:]
        case "press":
            try press(c["keys"] as? String ?? "", layout: c["layout"] as? String ?? "us")
            return [:]
        case "type":
            try type(c["text"] as? String ?? "")
            return [:]
        case "click":
            let found = try DriverFind.one(c)
            return ["under": try await click(found, count: c["count"] as? Int ?? 1)]
        case "drag":
            let from = try DriverFind.one(c["source"] as? [String: Any] ?? [:])
            let onto = try DriverFind.one(c["onto"] as? [String: Any] ?? [:])
            try drag(from, onto)
            return ["lane": "A: a drag inside the app only; lane A can't vouch for drag and drop between apps"]
        case "scroll":
            let found = try DriverFind.one(c)
            try scroll(found, dy: c["dy"] as? Double ?? -200)
            return [:]
        case "menu":
            let path = c["path"] as? [String] ?? []
            try chooseMenuItem(path, for: DevDriver.keyWindow())
            return ["lane": "A: the menu bar as AppKit validates it in the app; lane A can't vouch for the real one"]
        case "menu_item":
            let state = try menuItem(c["path"] as? [String] ?? [], for: DevDriver.keyWindow())
            return ["title": state.title, "enabled": state.enabled, "checked": state.checked]
        case "find":
            return ["found": try DriverFind.all(c).map(\.said)]
        case "shown":
            return ["shown": DriverShown.read()]
        case "tree":
            return ["tree": DriverFind.tree(c["within"] as? String)]
        case "picture":
            return ["path": try picture(c["path"] as? String), "approximate": true]
        case "settle":
            return try await DriverSettle.settle(ceiling: c["ceiling"] as? Double ?? 20)
        case "clock":
            let seconds = c["advance"] as? Double ?? 0
            await DevDriver.clock.advance(by: .milliseconds(Int64((seconds * 1000).rounded())))
            return ["now": ISO8601DateFormatter().string(from: DevDriver.clock.now)]
        case "core":
            return try await core(c)
        case "state":
            // Lane A as AppKit sees it: for finding out why a key or a click went nowhere.
            let window = DevDriver.keyWindow()
            return ["hidden": NSApp.isHidden, "active": NSApp.isActive, "key": window?.title ?? "",
                    "key_is_key": window?.isKeyWindow ?? false,
                    "first_responder": window?.firstResponder.map { "\(Swift.type(of: $0))" } ?? "",
                    "frame": window.map { NSStringFromRect($0.frame) } ?? "",
                    "windows": NSApp.orderedWindows.map { "\(Swift.type(of: $0)) “\($0.title)” visible=\($0.isVisible)" }]
        case "links":
            return ["opened": DevDriver.links.opened.map(\.absoluteString)]
        case "quit":
            await quit()
            return [:]
        default:
            throw Failed("no command \(verb.isEmpty ? "named" : "“\(verb)”")")
        }
    }

    // MARK: Keys

    static func window() throws -> NSWindow {
        guard let window = DevDriver.keyWindow() else { throw Failed("the app has no window open") }
        return window
    }

    /// Keys as a person presses them: "e", "cmd+shift+f", "pagedown"; several, apart: "g i".
    static func press(_ keys: String, layout name: String) throws {
        guard let layout = Self.layouts[name] else {
            throw Failed("no layout \(name): \(Self.layouts.keys.sorted().joined(separator: ", "))")
        }
        for chord in keys.split(separator: " ") {
            var held: NSEvent.ModifierFlags = []
            var key: UInt16?
            for part in chord.lowercased().split(separator: "+") {
                switch part {
                case "cmd", "command": held.insert(.command)
                case "shift": held.insert(.shift)
                case "alt", "option": held.insert(.option)
                case "ctrl", "control": held.insert(.control)
                default:
                    guard let code = Self.codes[String(part)] else { throw Failed("no key “\(part)”") }
                    key = code
                }
            }
            guard let key else { throw Failed("“\(chord)” names no key") }
            try RealKeys.press(key, held: held, layout: layout, in: try window())
        }
    }

    /// Words typed where the keyboard is, a key at a time under the US layout; a character it has no key for comes as
    /// a key typing it, as an input method hands one over.
    static func type(_ text: String) throws {
        let window = try window()
        for character in text {
            if character == "\n" {
                try RealKeys.press(UInt16(kVK_Return), layout: .us, in: window)
            } else if let (code, held) = typing[character] {
                try RealKeys.press(code, held: held, layout: .us, in: window)
            } else {
                try RealKeys.press(String(character), 0, in: window)
            }
        }
    }

    static let layouts: [String: KeyboardLayout] = [
        "us": .us, "arabic": .arabic, "swiss-french": .swissFrench, "german": .german,
    ]

    /// The keys a press names, by their code: the letters, digits and marks where the US layout has them, and the
    /// keys that type nothing.
    static let codes: [String: UInt16] = {
        var codes: [String: UInt16] = [
            "enter": UInt16(kVK_Return), "return": UInt16(kVK_Return), "tab": UInt16(kVK_Tab),
            "space": UInt16(kVK_Space), "backspace": UInt16(kVK_Delete), "delete": UInt16(kVK_ForwardDelete),
            "escape": UInt16(kVK_Escape), "esc": UInt16(kVK_Escape), "up": UInt16(kVK_UpArrow),
            "down": UInt16(kVK_DownArrow), "left": UInt16(kVK_LeftArrow), "right": UInt16(kVK_RightArrow),
            "pageup": UInt16(kVK_PageUp), "pagedown": UInt16(kVK_PageDown), "home": UInt16(kVK_Home),
            "end": UInt16(kVK_End),
        ]
        for code in UInt16(0)..<UInt16(128) {
            guard let typed = try? KeyboardLayout.us.characters(code).characters, typed.count == 1,
                  let scalar = typed.unicodeScalars.first, scalar.value > 0x20, scalar.value < 0x7F
            else { continue }
            codes[typed] = codes[typed] ?? code
        }
        return codes
    }()

    /// Each character the US layout types, and the key and modifiers that type it.
    static let typing: [Character: (UInt16, NSEvent.ModifierFlags)] = {
        var found: [Character: (UInt16, NSEvent.ModifierFlags)] = [:]
        for held in [NSEvent.ModifierFlags(), .shift] {
            for code in UInt16(0)..<UInt16(128) {
                guard let typed = try? KeyboardLayout.us.characters(code, held: held).characters, typed.count == 1,
                      let scalar = typed.unicodeScalars.first, scalar.value >= 0x20, scalar.value < 0x7F,
                      let character = typed.first, found[character] == nil
                else { continue }
                found[character] = (code, held)
            }
        }
        return found
    }()

    // MARK: The mouse

    @discardableResult
    static func click(_ target: DriverFind.Match, count: Int) async throws -> String {
        guard let window = target.window else { throw Failed("\(target.found) is in no window") }
        guard let content = window.contentView else { throw Failed("the window has no content") }
        if let cell = target.found.element as? MailRowCell {
            return try await click(cell, in: window, count: count)
        }
        guard target.visible else { throw Failed("\(target.found) isn't visible: a person couldn't click it") }
        let point = try target.found.middle(in: content)
        let at = content.convert(point, to: nil)
        let under = RealMouse.view(at: at, in: window)
        if let under, Self.isSwiftUI(under) {
            // SwiftUI's views take a click as the window hands it on, through their gestures, never in a view's own
            // `mouseDown`: it goes to the window, as AppKit sends a click in a key window.
            try clickThroughWindow(at: at, in: window, count: count) { window.sendEvent($0) }
        } else {
            try RealMouse.click(at: point, in: content, count: count)
        }
        return under.map { "\(Swift.type(of: $0))" } ?? "nothing"
    }

    /// A row of the list clicked as a person clicks one, as the hosted tests click it: scrolled into sight where it
    /// is out of it, then on its text, below any day's head and off its buttons and check box.
    private static func click(_ found: MailRowCell, in window: NSWindow, count: Int) async throws -> String {
        guard let key = found.row?.key else { throw Failed("the row hasn't come yet") }
        var tables: [NSTableView] = []
        func look(_ view: NSView) {
            if let table = view as? NSTableView { tables.append(table) }
            view.subviews.forEach(look)
        }
        window.contentView.map(look)
        guard let (table, at) = tables.lazy.compactMap({ table in
            (0..<table.numberOfRows).first { (table.view(atColumn: 0, row: $0, makeIfNecessary: true) as? MailRowCell)?
                .row?.key == key }.map { (table, $0) }
        }).first else { throw Failed("the row has left the list") }
        table.scrollRowToVisible(at)
        window.contentView?.layoutSubtreeIfNeeded()
        guard let cell = table.view(atColumn: 0, row: at, makeIfNecessary: false) as? MailRowCell,
              DriverFind.visible(Found(element: cell, role: "AXRow", label: "", identifier: ""), in: window)
        else { throw Failed("row \(at + 1) can't be scrolled into sight") }
        let bounds = cell.bounds
        let y = cell.isFlipped ? bounds.maxY - 8 : bounds.minY + 8
        let point = [0.45, 0.55, 0.35, 0.65].map { CGPoint(x: bounds.minX + bounds.width * $0, y: y) }
            .first { cell.shownTarget(at: $0) == nil && !cell.inHead($0) } ?? CGPoint(x: bounds.midX, y: y)
        let row = table.row(for: cell)
        guard row >= 0 else { throw Failed("the row has left the table") }
        guard count == 1 else { throw Failed("lane A can't double-click a row: the table counts a real mouse's clicks") }
        try RealMouse.click(at: point, in: cell, count: count)
        // The table's own tracking of a click on a row that can be dragged reads the Mac's real mouse, which lane A
        // never presses, and chooses nothing: the row is chosen as the table chooses one at a plain click, through
        // its delegate. The hosted tests (lane B) hold the table's own click.
        if !table.selectedRowIndexes.contains(row) {
            let proposed = IndexSet(integer: row)
            let chosen = table.delegate?.tableView?(table, selectionIndexesForProposedSelection: proposed) ?? proposed
            if table.acceptsFirstResponder { table.window?.makeFirstResponder(table) }
            table.selectRowIndexes(chosen, byExtendingSelection: false)
        }
        return "row \(row + 1), chosen as the table chooses one (lane A presses no real mouse)"
    }

    private static func table(_ view: NSView) -> NSTableView? {
        var next: NSView? = view
        while let at = next {
            if let table = at as? NSTableView { return table }
            next = at.superview
        }
        return nil
    }

    private static func isSwiftUI(_ view: NSView) -> Bool {
        var next: NSView? = view
        while let at = next {
            let name = String(describing: Swift.type(of: at))
            if name.contains("Hosting") || name.hasPrefix("_") && name.contains("SwiftUI") { return true }
            if at is NSControl || at is NSTableView || at is NSTextView { return false }
            next = at.superview
        }
        return false
    }

    /// A click as the window hands one on: each event posted into the app's queue, placed so that it comes out where
    /// it was made (`RealMouse.placed`), taken out, so it is the app's current event, and handed to `deliver`. What
    /// tracks the mouse takes the rest of the gesture from the queue itself.
    private static func clickThroughWindow(at point: CGPoint, in window: NSWindow, count: Int,
                                           deliver: (NSEvent) -> Void) throws {
        var made: [NSEvent] = []
        for click in 1...max(count, 1) {
            made.append(try RealMouse.event(.leftMouseDown, at: point, in: window, clicks: click))
            made.append(try RealMouse.event(.leftMouseUp, at: point, in: window, clicks: click))
        }
        let gesture = try RealMouse.placed(made, in: window)
        let mask: NSEvent.EventTypeMask = [.leftMouseDown, .leftMouseUp, .leftMouseDragged]
        while NSApp.nextEvent(matching: mask, until: .distantPast, inMode: .default, dequeue: true) != nil {}
        for event in gesture { NSApp.postEvent(event, atStart: false) }
        var left = gesture.count
        while left > 0,
              let next = NSApp.nextEvent(matching: mask, until: Date(timeIntervalSinceNow: 0.05), inMode: .default,
                                         dequeue: true)
        {
            left -= 1
            deliver(next)
        }
    }

    static func drag(_ from: DriverFind.Match, _ onto: DriverFind.Match) throws {
        guard let window = from.window, let content = window.contentView else { throw Failed("no window to drag in") }
        try RealMouse.drag(from: try from.found.middle(in: content), to: try onto.found.middle(in: content),
                           in: content)
    }

    static func scroll(_ over: DriverFind.Match, dy: Double) throws {
        guard let window = over.window, let content = window.contentView else { throw Failed("no window to scroll") }
        try RealMouse.scroll(by: CGVector(dx: 0, dy: dy), at: try over.found.middle(in: content), in: content)
    }

    // MARK: A picture

    /// The key window drawn by itself into a PNG: approximate, as no screen is captured in lane A (a table's rows and
    /// the sidebar's material can come out blank).
    static func picture(_ path: String?) throws -> String {
        let window = try window()
        guard let view = window.contentView?.superview ?? window.contentView else { throw Failed("no content") }
        guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { throw Failed("no bitmap") }
        view.cacheDisplay(in: view.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { throw Failed("no PNG") }
        let out = path ?? (NSTemporaryDirectory() as NSString).appendingPathComponent("mm-mac-\(UUID().uuidString).png")
        try png.write(to: URL(fileURLWithPath: out))
        return out
    }

    // MARK: The core

    static func core(_ c: [String: Any]) async throws -> [String: Any] {
        guard let core = MailApp.shared.core else { throw Failed("the core isn't open yet") }
        let host = core.testHost
        let op = c["op"] as? String ?? ""
        if op == "route" {
            let body = (c["body"] as? String).map { Data($0.utf8) } ?? Data()
            let answer = await core.send(c["method"] as? String ?? "GET", c["target"] as? String ?? "/", body)
            return ["status": Int(answer.status), "content_type": answer.contentType,
                    "body": String(decoding: answer.body, as: UTF8.self)]
        }
        let args = c["args"] as? [String] ?? []
        let seconds = c["seconds"] as? Double ?? 0
        let at = c["at"] as? String ?? ""
        let said: String = try await offMain {
            switch op {
            case "now": return try host.testNow()
            case "set":
                try host.testSetClock(at: at)
                return try host.testNow()
            case "advance": return try host.testAdvanceClock(seconds: seconds)
            case "kick":
                try host.testKick(job: args.first ?? "")
                return ""
            case "pump":
                try host.testPump(job: args.first ?? "", arguments: Array(args.dropFirst()))
                return ""
            case "kicked":
                try host.testPumpKicked()
                return ""
            case "settle": return String(try host.testSettle(timeoutSeconds: seconds > 0 ? seconds : 30))
            case "high_water": return try host.testHighWater()
            default: throw Failed("no core op “\(op)”")
            }
        }
        return ["said": said]
    }

    /// Work the core does on the caller's thread, off the main one, so the app goes on as it runs.
    static func offMain<T: Sendable>(_ work: @escaping @Sendable () throws -> T) async throws -> T {
        try await withCheckedThrowingContinuation { done in
            DispatchQueue.global(qos: .userInitiated).async { done.resume(with: Result { try work() }) }
        }
    }

    /// The app ends: the core closed (its requests answered, its work stopped), then the process.
    static func quit() async {
        if let core = MailApp.shared.core { await core.close() }
        DispatchQueue.global().asyncAfter(deadline: .now() + .milliseconds(100)) { exit(0) }
    }
}
#endif
