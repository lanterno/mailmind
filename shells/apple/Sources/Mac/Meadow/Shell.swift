import Foundation
import Observation

// Meadow's shell (#44), the mockup's `.shell`: the rail, the list and the pane as panels of paper on the sky, 8 apart
// and 8 from the window's edges below the top bar. The rail is icons (64) or icons and names (224), and under 1100 wide
// a drawer over the window; the list is 372 wide, 300 to 560 with the splitter; focus reading shows only the pane.

/// Where each panel stands in the shell, below the top bar, for one window width and one state of the shell. Every
/// rectangle is in the shell's own coordinates, with the top bar's foot at y = 0.
struct ShellLayout: Equatable {
    static let gap: CGFloat = 8
    static let topBar: CGFloat = 56
    static let railFolded: CGFloat = 64
    static let railOpen: CGFloat = 224
    static let listMin: CGFloat = 300
    static let listMax: CGFloat = 560
    static let listStart: CGFloat = 372
    /// What one press of an arrow key moves the splitter by, as the page's does.
    static let listStep: CGFloat = 24
    /// At this width and under, the rail leaves the row and becomes a drawer.
    static let drawerAtMost: CGFloat = 1100

    /// What the shell shows.
    enum Mode: Equatable {
        /// The rail, the list and the pane.
        case columns
        /// Focus reading: only the pane.
        case focus
        /// A place of its own across the list's and the pane's room (the gate, the paper trail), beside the rail.
        case full
    }

    /// The rail in the row; nil where it is a drawer or focus reading hides it.
    let rail: CGRect?
    let list: CGRect?
    let pane: CGRect?
    let full: CGRect?
    /// The splitter, in the gap between the list and the pane.
    let splitter: CGRect?
    /// The drawer the rail becomes in a narrow window, open.
    let drawer: CGRect
    let docked: Bool

    /// Whether a window this wide keeps the rail in the row.
    static func docks(_ width: CGFloat) -> Bool { width > drawerAtMost }

    /// A list width a person asked for, kept within the splitter's limits.
    static func clampList(_ width: CGFloat) -> CGFloat {
        guard width.isFinite else { return listStart }
        return min(listMax, max(listMin, width.rounded()))
    }

    init(size: CGSize, railOpen: Bool, listWidth: CGFloat, mode: Mode) {
        let gap = Self.gap
        let height = max(0, size.height - gap)
        docked = Self.docks(size.width)
        let railWidth = docked ? (railOpen ? Self.railOpen : Self.railFolded) : 0
        let listWidth = Self.clampList(listWidth)
        drawer = CGRect(x: 0, y: 0, width: min(290, (size.width * 0.86).rounded(.down)), height: size.height)
        let railRect = docked ? CGRect(x: gap, y: 0, width: railWidth, height: height) : nil
        let start = railRect.map { $0.maxX + gap } ?? gap
        let end = size.width - gap
        switch mode {
        case .focus:
            rail = nil
            list = nil
            full = nil
            splitter = nil
            pane = CGRect(x: gap, y: 0, width: max(0, end - gap), height: height)
        case .full:
            rail = railRect
            list = nil
            pane = nil
            splitter = nil
            full = CGRect(x: start, y: 0, width: max(0, end - start), height: height)
        case .columns:
            rail = railRect
            let listRect = CGRect(x: start, y: 0, width: listWidth, height: height)
            list = listRect
            full = nil
            splitter = CGRect(x: listRect.maxX, y: 0, width: gap, height: height)
            pane = CGRect(x: listRect.maxX + gap, y: 0, width: max(0, end - listRect.maxX - gap), height: height)
        }
    }
}

/// The shell's state a person sets: the rail open or folded and the list's width, each remembered on this Mac, and
/// for the moment focus reading and the drawer. The window's width says whether the rail is in the row.
@MainActor
@Observable
final class MeadowShell {
    static let railKey = "meadow.rail-open"
    static let listKey = "meadow.list-width"

    @ObservationIgnored private let defaults: UserDefaults

    /// The rail shows names as well as icons (`[` or the sidebar button, in a wide window).
    var railOpen: Bool {
        didSet { defaults.set(railOpen, forKey: Self.railKey) }
    }

    /// The list's width, within the splitter's limits.
    private(set) var listWidth: CGFloat {
        didSet { defaults.set(Double(listWidth), forKey: Self.listKey) }
    }

    /// Focus reading: only the pane (Enter, or the pane bar's expand button; Esc leaves).
    var focus = false
    /// The rail as a drawer over a narrow window, open.
    var drawerOpen = false
    /// The window's width, which says whether the rail is in the row.
    var width: CGFloat = 1440 {
        didSet { if docked { drawerOpen = false } }
    }

    var docked: Bool { ShellLayout.docks(width) }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        railOpen = defaults.bool(forKey: Self.railKey)
        let saved = defaults.double(forKey: Self.listKey)
        listWidth = saved > 0 ? ShellLayout.clampList(saved) : ShellLayout.listStart
    }

    /// The sidebar button and `[`: names on or off in a wide window, the drawer in a narrow one. Asking for the
    /// mailboxes leaves focus reading.
    func toggleRail() {
        focus = false
        if docked {
            railOpen.toggle()
        } else {
            drawerOpen.toggle()
        }
    }

    func setListWidth(_ width: CGFloat) {
        let clamped = ShellLayout.clampList(width)
        if clamped != listWidth { listWidth = clamped }
    }

    /// The splitter's arrow keys: a step wider or narrower.
    func nudgeList(by steps: Int) {
        setListWidth(listWidth + CGFloat(steps) * ShellLayout.listStep)
    }

    /// A double-click on the splitter: the list's own width again.
    func resetList() {
        setListWidth(ShellLayout.listStart)
    }

    func toggleFocus() {
        focus.toggle()
        if focus { drawerOpen = false }
    }

    /// Esc: close the drawer, else leave focus reading. Whether it did either.
    @discardableResult
    func escape() -> Bool {
        if drawerOpen {
            drawerOpen = false
            return true
        }
        if focus {
            focus = false
            return true
        }
        return false
    }

    /// What the shell shows, for a place that spans the window or not.
    func mode(spanning: Bool) -> ShellLayout.Mode {
        if spanning { return .full }
        return focus ? .focus : .columns
    }
}

/// How the pane bar folds as the pane narrows (the mockup's `@container pane` rules): the keycaps go first, then the
/// second labels (Later, All, Forward), then every label, then the second buttons and the position.
struct PaneBarFold: Hashable {
    /// The keycaps beside the words (wider than 980).
    let keys: Bool
    /// Later's, All's and Forward's words (wider than 820).
    let secondLabels: Bool
    /// Every button's words (wider than 720); without them each is a square icon.
    let labels: Bool
    /// Later, All and Forward, their separator, and "n of N" (wider than 480).
    let secondary: Bool

    init(width: CGFloat) {
        keys = width > 980
        secondLabels = width > 820
        labels = width > 720
        secondary = width > 480
    }

    init(keys: Bool, secondLabels: Bool, labels: Bool, secondary: Bool) {
        self.keys = keys
        self.secondLabels = secondLabels
        self.labels = labels
        self.secondary = secondary
    }

    /// This fold, then each further one, in the order the bar gives things up: the keycaps, the second labels, every
    /// label, the second buttons. The mockup's widths are the page's faces' measures, not the Mac's, so the bar shows
    /// the first of these that fits (`MeadowPaneBar`), and a word gives way whole, never cut short.
    var further: [PaneBarFold] {
        var folds: [PaneBarFold] = []
        for step in 0...4 {
            let fold = PaneBarFold(keys: keys && step < 1, secondLabels: secondLabels && step < 2,
                                   labels: labels && step < 3, secondary: secondary && step < 4)
            if folds.last != fold { folds.append(fold) }
        }
        return folds
    }

    /// The bar folded as far as it goes: the buttons as icons, without the second ones or the position.
    static let folded = PaneBarFold(keys: false, secondLabels: false, labels: false, secondary: false)
}

extension DeskPlace {
    /// The screens that stand in the reading pane beside the Inbox's list, as the mockup shows the brief. An answer
    /// stays in the list's place, so the mail it cites opens in the pane beside it.
    var besideTheInbox: Bool {
        switch self {
        case .brief: true
        case .ask, .screener: false
        }
    }
}

extension Place {
    /// The place whose list the window shows: the Inbox's beside a screen in the pane, else the place's own.
    var listed: Place {
        if case .desk(let screen) = self, screen.besideTheInbox { return .allInboxes }
        return self
    }

    /// A place that is a page of its own across the list's and the pane's room, as the mockup shows the gate and the
    /// paper trail.
    var spansTheWindow: Bool {
        switch self {
        case .desk(.screener): true
        case .search(let search): search.lane == .papertrail
        default: false
        }
    }
}
