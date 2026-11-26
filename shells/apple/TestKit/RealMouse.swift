import AppKit

/// The mouse as a person uses it: clicks, drags, the wheel and the pointer's moves made as real events, posted into the
/// app's queue and taken out as the app's run takes them, so each is the app's current event as it is handled. A
/// control that tracks the mouse itself (a button, a slider) takes the rest of its gesture from the queue as it would
/// from a person's hand, so every event of a gesture is posted before the first is handed on.
///
/// In a key window of an active app, each event goes to the window, which routes it. A test's app is never active
/// (its windows are never key), and a window that isn't key keeps a first click to itself, to become key: so there
/// the kit routes a click as the key window would, to the view under the pointer (made first responder where it
/// accepts that), and the rest of its gesture to that same view. The wheel goes to the window either way, as macOS
/// scrolls a window behind others.
///
/// Points are in the coordinates of the view named, which must be in a window.
@MainActor
enum RealMouse {
    /// The button a click is made with.
    enum Button: Sendable {
        case left
        case middle
    }

    /// A click at `point` in `view`; `count` 2 is a double click, its second click counted as AppKit counts one.
    static func click(at point: CGPoint, in view: NSView, count: Int = 1, button: Button = .left,
                      held: NSEvent.ModifierFlags = []) throws
    {
        let window = try window(of: view)
        let at = view.convert(point, to: nil)
        let (down, up): (NSEvent.EventType, NSEvent.EventType) = button == .left
            ? (.leftMouseDown, .leftMouseUp) : (.otherMouseDown, .otherMouseUp)
        var gesture: [NSEvent] = []
        for click in 1...max(count, 1) {
            gesture.append(try event(down, at: at, in: window, clicks: click, held: held, button: button))
            gesture.append(try event(up, at: at, in: window, clicks: click, held: held, button: button))
        }
        try send(gesture, in: window)
    }

    /// A click in the middle of `view`.
    static func click(_ view: NSView, count: Int = 1, button: Button = .left, held: NSEvent.ModifierFlags = []) throws {
        try click(at: CGPoint(x: view.bounds.midX, y: view.bounds.midY), in: view, count: count, button: button,
                  held: held)
    }

    /// A click on what `found` names, in its middle.
    static func click(_ found: Found, in window: NSWindow, count: Int = 1) throws {
        guard let content = window.contentView else { throw KitError("the window has no content") }
        try click(at: try found.middle(in: content), in: content, count: count)
    }

    /// The left button pressed at `from`, moved in `steps` to `to`, and let go there.
    static func drag(from: CGPoint, to: CGPoint, in view: NSView, steps: Int = 8) throws {
        let window = try window(of: view)
        let start = view.convert(from, to: nil)
        let end = view.convert(to, to: nil)
        var gesture = [try event(.leftMouseDown, at: start, in: window)]
        let count = max(steps, 1)
        for step in 1...count {
            let part = CGFloat(step) / CGFloat(count)
            let at = CGPoint(x: start.x + (end.x - start.x) * part, y: start.y + (end.y - start.y) * part)
            gesture.append(try event(.leftMouseDragged, at: at, in: window))
        }
        gesture.append(try event(.leftMouseUp, at: end, in: window))
        try send(gesture, in: window)
    }

    /// The wheel turned over `point` in `view` by `delta` points, as a trackpad scrolls: a positive `dy` shows what is
    /// further up, as turning the wheel towards you does.
    static func scroll(by delta: CGVector, at point: CGPoint, in view: NSView) throws {
        let window = try window(of: view)
        guard let made = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 2,
                                 wheel1: Int32(delta.dy.rounded()), wheel2: Int32(delta.dx.rounded()), wheel3: 0)
        else { throw KitError("Core Graphics made no scroll event") }
        made.location = globalPoint(window.convertPoint(toScreen: view.convert(point, to: nil)))
        made.setIntegerValueField(.mouseEventWindowUnderMousePointer, value: Int64(window.windowNumber))
        made.setIntegerValueField(.mouseEventWindowUnderMousePointerThatCanHandleThisEvent,
                                  value: Int64(window.windowNumber))
        guard let event = NSEvent(cgEvent: made) else { throw KitError("AppKit read no scroll event") }
        try post([event]) { window.sendEvent($0) }
    }

    /// The pointer moved to `point` over `view`, as its tracking areas that asked for moves hear it.
    static func move(to point: CGPoint, in view: NSView) throws {
        let window = try window(of: view)
        let moved = try event(.mouseMoved, at: view.convert(point, to: nil), in: window)
        let owners = try owners(of: view, asking: .mouseMoved)
        try post(try placed([moved], in: window)) { event in owners.forEach { $0.mouseMoved(with: event) } }
    }

    /// The pointer leaving `view`, as its tracking areas that asked for entering and leaving hear it.
    static func leave(_ view: NSView) throws {
        let window = try window(of: view)
        guard let left = NSEvent.enterExitEvent(
            with: .mouseExited, location: .zero, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: window.windowNumber, context: nil, eventNumber: 0, trackingNumber: 0, userData: nil)
        else { throw KitError("AppKit made no exit event") }
        let owners = try owners(of: view, asking: .mouseEnteredAndExited)
        try post([left]) { event in owners.forEach { $0.mouseExited(with: event) } }
    }

    /// A mouse event at `point` in `window`'s coordinates, as AppKit makes one.
    static func event(_ type: NSEvent.EventType, at point: CGPoint, in window: NSWindow, clicks: Int = 1,
                      held: NSEvent.ModifierFlags = [], button: Button = .left) throws -> NSEvent
    {
        let pressed: Set<NSEvent.EventType> = [.leftMouseDown, .leftMouseDragged, .otherMouseDown, .otherMouseDragged]
        let counted: Set<NSEvent.EventType> = [.leftMouseDown, .leftMouseUp, .otherMouseDown, .otherMouseUp]
        guard let event = NSEvent.mouseEvent(
            with: type, location: point, modifierFlags: held, timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: window.windowNumber, context: nil, eventNumber: 0,
            clickCount: counted.contains(type) ? clicks : 0, pressure: pressed.contains(type) ? 1 : 0)
        else { throw KitError("AppKit made no \(type) event") }
        guard button == .middle else { return event }
        // AppKit makes the other button's events with no button number: the middle one is number 2.
        guard let cg = event.cgEvent else { throw KitError("the event has no Core Graphics form") }
        cg.setIntegerValueField(.mouseEventButtonNumber, value: 2)
        guard let middle = NSEvent(cgEvent: cg) else { throw KitError("AppKit read no middle-button event") }
        return middle
    }

    /// `gesture` posted and handed on: to the window where it is key in an active app, else as the key window would
    /// route it.
    static func send(_ gesture: [NSEvent], in window: NSWindow) throws {
        let gesture = try placed(gesture, in: window)
        if NSApp.isActive, window.isKeyWindow {
            try post(gesture) { window.sendEvent($0) }
            return
        }
        var pressedOn: NSView?
        try post(gesture) { event in
            switch event.type {
            case .leftMouseDown, .otherMouseDown:
                guard let under = view(at: event.locationInWindow, in: window) else { return }
                pressedOn = under
                if event.type == .leftMouseDown, under.acceptsFirstResponder, window.firstResponder !== under {
                    window.makeFirstResponder(under)
                }
                if event.type == .leftMouseDown { under.mouseDown(with: event) } else { under.otherMouseDown(with: event) }
            case .leftMouseDragged: (pressedOn ?? view(at: event.locationInWindow, in: window))?.mouseDragged(with: event)
            case .leftMouseUp: (pressedOn ?? view(at: event.locationInWindow, in: window))?.mouseUp(with: event)
            case .otherMouseUp: (pressedOn ?? view(at: event.locationInWindow, in: window))?.otherMouseUp(with: event)
            default: window.sendEvent(event)
            }
        }
    }

    /// The view under `point` in `window`'s coordinates, as the window hit-tests a click.
    static func view(at point: CGPoint, in window: NSWindow) -> NSView? {
        guard let content = window.contentView else { return nil }
        let top = content.superview ?? content
        return top.hitTest(top.superview.map { top.convert(point, to: $0) } ?? point)
    }

    /// `events` moved so that each comes out of the app's queue where it was made. A posted event's place goes through
    /// the screen and back, by where the window server holds the window, which for a window far off the screen isn't
    /// quite where AppKit holds it: a probe posted and taken out at once says by how much.
    static func placed(_ events: [NSEvent], in window: NSWindow) throws -> [NSEvent] {
        guard let first = events.first else { return events }
        let probe = try event(.mouseMoved, at: first.locationInWindow, in: window)
        while NSApp.nextEvent(matching: .mouseMoved, until: .distantPast, inMode: .default, dequeue: true) != nil {}
        NSApp.postEvent(probe, atStart: true)
        guard let back = NSApp.nextEvent(matching: .mouseMoved, until: Date(timeIntervalSinceNow: 1), inMode: .default,
                                         dequeue: true)
        else { throw KitError("a probe posted never came out of the app's queue") }
        let dx = back.locationInWindow.x - first.locationInWindow.x
        let dy = back.locationInWindow.y - first.locationInWindow.y
        guard abs(dx) > 0.001 || abs(dy) > 0.001 else { return events }
        return try events.map { made in
            let at = CGPoint(x: made.locationInWindow.x - dx, y: made.locationInWindow.y - dy)
            return try event(made.type, at: at, in: window, clicks: made.type == .mouseMoved ? 1 : made.clickCount,
                             held: made.modifierFlags, button: made.buttonNumber == 2 ? .middle : .left)
        }
    }

    /// `events` posted into the app's queue in order, then each taken out and handed to `handle`, unless a control
    /// tracking the mouse took it from the queue first. What an earlier gesture left in the queue is cleared first.
    private static func post(_ events: [NSEvent], _ handle: (NSEvent) -> Void) throws {
        let mask = events.reduce(into: NSEvent.EventTypeMask()) { $0.insert(NSEvent.EventTypeMask(type: $1.type)) }
        while NSApp.nextEvent(matching: mask, until: .distantPast, inMode: .default, dequeue: true) != nil {}
        for event in events { NSApp.postEvent(event, atStart: false) }
        var left = events.count
        while left > 0,
              let next = NSApp.nextEvent(matching: mask, until: Date(timeIntervalSinceNow: 0.05), inMode: .default,
                                         dequeue: true)
        {
            left -= 1
            handle(next)
        }
    }

    /// The owners of `view`'s tracking areas that asked for `asked`, which AppKit hands such an event to.
    private static func owners(of view: NSView, asking asked: NSTrackingArea.Options) throws -> [NSResponder] {
        let owners = view.trackingAreas.filter { $0.options.contains(asked) }.compactMap { $0.owner as? NSResponder }
        guard !owners.isEmpty else { throw KitError("no tracking area of \(type(of: view)) follows the pointer") }
        return owners
    }

    private static func window(of view: NSView) throws -> NSWindow {
        guard let window = view.window else { throw KitError("the view is in no window") }
        return window
    }

    /// A point on AppKit's screens (origin at the bottom left of the main screen) as Core Graphics places one
    /// (origin at its top left).
    private static func globalPoint(_ point: CGPoint) -> CGPoint {
        let height = NSScreen.screens.first?.frame.height ?? 0
        return CGPoint(x: point.x, y: height - point.y)
    }
}
