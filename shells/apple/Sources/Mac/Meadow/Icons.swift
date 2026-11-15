import SwiftUI
#if os(macOS)
import AppKit
#endif

// Meadow's icons (#44): one stroke family on a 24-point grid, stroked 1.75 wide with round ends and corners and filled
// nowhere, as the mockup's `.ic` draws its <symbol>s. Each icon is the symbol's own SVG, read once (SVGPath).

/// Every icon Meadow draws, named as the mockup names its symbols.
enum MeadowIcon: String, CaseIterable, Sendable {
    // The mockup's.
    case sun, moon, dusk, inbox, gate, hourglass, bookmark, clock, leaf, receipt, clip, folder, search, pen, archive
    case trash, reply, replyAll = "replyall", forward, more, check, x, down, up, left, right, wind, shield, alert, cal
    case sliders, undo, send, cloudOff = "cloud-off", refresh, keyboard, menu, star, eye, book, phone, tag, mail, plus
    case user, ban, bell, external, expand, shrink, aa, restore, sidebar
    // The page's, drawn for what the mockup shows no icon for (web/index.html's sprite): mute, a pinned place, a
    // download, attachments' kinds, a kept secret, a conversation, lists and tasks.
    case bellOff = "bell-off", pin, download, file, image, table, slides, lock, key, chat, list, todo
    // The app's own, in the same hand, for what neither has: Settings' panes, help, copying, links, filters, adding to
    // the calendar, a task's state, the outbox and the command key.
    case gear, at, info, question, minus, copy, link, filter, calAdd = "cal-add", unlock, circle
    case checkCircle = "check-circle", mailOpen = "mail-open", outbox, command, arrowRight = "arrow-right"

    /// The symbol's shapes, as its SVG has them.
    var parts: [SVGPart] {
        switch self {
        case .sun: [.circle(12, 12, 4),
                    .path("M12 2.5v2M12 19.5v2M4.6 4.6 6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18"
                          + "M18 6l1.4-1.4")]
        case .moon: [.path("M20 14.6A8.2 8.2 0 1 1 9.4 4a6.6 6.6 0 0 0 10.6 10.6z")]
        case .dusk: [.path("M3 18h18M6 14.5a6 6 0 0 1 12 0M12 4.5v2.5M4.9 8.4l1.6 1.4M19.1 8.4l-1.6 1.4M7 21.5h10")]
        case .inbox: [.path("M3.5 13.5h4.6l1.6 2.5h4.6l1.6-2.5h4.6"),
                      .path("M6.2 5h11.6l2.7 8.5V18a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-4.5z")]
        case .gate: [.path("M4 21V7.5M20 21V7.5M2.5 8.5 4 7l1.5 1.5M18.5 8.5 20 7l1.5 1.5M4 11.5h16M4 17h16M8 11.5V21"
                           + "M12 11.5V21M16 11.5V21")]
        case .hourglass: [.path("M6 3h12M6 21h12M7.5 3v3.2a4.5 4.5 0 0 0 9 0V3M7.5 21v-3.2a4.5 4.5 0 0 1 9 0V21")]
        case .bookmark: [.path("M6.5 3.5h11v17l-5.5-4-5.5 4z")]
        case .clock: [.circle(12, 12, 8.5), .path("M12 7.5V12l3 2")]
        case .leaf: [.path("M5 19.5C5 11 10 5 20 4.5c0 10-6 15.5-14 15.5z"), .path("M5 19.5c3-4.5 6.5-7.5 10.5-9.5")]
        case .receipt: [.path("M6 3h12v18l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4L6 21z"), .path("M9 8h6M9 11.5h6M9 15h3.5")]
        case .clip: [.path("m20 11.3-8.1 8.1a5 5 0 0 1-7.1-7.1l8.4-8.4a3.3 3.3 0 0 1 4.7 4.7l-8.4 8.4"
                           + "a1.7 1.7 0 0 1-2.4-2.4l7.7-7.7")]
        case .folder: [.path("M3.5 7a2 2 0 0 1 2-2h3.8l2 2.2h7.2a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z")]
        case .search: [.circle(11, 11, 6.5), .path("m20 20-4.2-4.2")]
        case .pen: [.path("M4 20h4L19.2 8.8a2.8 2.8 0 0 0-4-4L4 16z"), .path("m13.5 6.5 4 4")]
        case .archive: [.rect(3, 4.5, 18, 4.5, 1.2),
                        .path("M5 9v9.5a1.5 1.5 0 0 0 1.5 1.5h11a1.5 1.5 0 0 0 1.5-1.5V9M10 13h4")]
        case .trash: [.path("M4 7h16M9.5 7V4.5h5V7M6 7l1 12.5a1.5 1.5 0 0 0 1.5 1.5h7a1.5 1.5 0 0 0 1.5-1.5L18 7")]
        case .reply: [.path("M9.5 6.5 4 12l5.5 5.5"), .path("M4 12h10a6 6 0 0 1 6 6v1")]
        case .replyAll: [.path("M12 6.5 6.5 12l5.5 5.5M7.5 6.5 2 12l5.5 5.5"), .path("M6.5 12H14a6 6 0 0 1 6 6v1")]
        case .forward: [.path("M14.5 6.5 20 12l-5.5 5.5"), .path("M20 12H10a6 6 0 0 0-6 6v1")]
        case .more: [.circle(5.5, 12, 1.2), .circle(12, 12, 1.2), .circle(18.5, 12, 1.2)]
        case .check: [.path("m5 12.5 4.5 4.5L19 7.5")]
        case .x: [.path("M6.5 6.5l11 11M17.5 6.5l-11 11")]
        case .down: [.path("m6.5 9.5 5.5 5.5 5.5-5.5")]
        case .up: [.path("m6.5 14.5 5.5-5.5 5.5 5.5")]
        case .left: [.path("M14.5 6.5 9 12l5.5 5.5")]
        case .right: [.path("m9.5 6.5 5.5 5.5-5.5 5.5")]
        case .wind: [.path("M3 9h10.5a3 3 0 1 0-3-3"), .path("M3 13h15a3 3 0 1 1-3 3"), .path("M3 17h6")]
        case .shield: [.path("M12 3.2 19.5 6v5.6c0 4.6-3.2 7.8-7.5 9.2-4.3-1.4-7.5-4.6-7.5-9.2V6z"),
                       .path("m9 12 2.2 2.2L15.5 10")]
        case .alert: [.path("M12 3.5 21.5 20h-19z"), .path("M12 10v4.5M12 17.3v.2")]
        case .cal: [.rect(3.5, 5, 17, 15.5, 2), .path("M3.5 9.5h17M8 3v4M16 3v4")]
        case .sliders: [.path("M4 7h9M17 7h3M4 17h3M11 17h9"), .circle(15, 7, 2), .circle(9, 17, 2)]
        case .undo: [.path("M9 14.5 4 9.5l5-5"), .path("M4 9.5h10.5a5.5 5.5 0 0 1 0 11H11")]
        case .send: [.path("M20.5 3.5 10 14M20.5 3.5 14 20.5l-4-6.5-6.5-4z")]
        case .cloudOff: [.path("M3 3l18 18"),
                         .path("M8.5 6.3A6 6 0 0 1 17.6 10 4 4 0 0 1 20 17M15.5 18.5H7a4.5 4.5 0 0 1-1.6-8.7")]
        case .refresh: [.path("M20 11a8 8 0 0 0-14.3-4.6L4 8.5M4 13a8 8 0 0 0 14.3 4.6l1.7-2.1"),
                        .path("M4 4v4.5h4.5M20 20v-4.5h-4.5")]
        case .keyboard: [.rect(2.5, 6, 19, 12, 2),
                         .path("M6 10h.01M9.3 10h.01M12.6 10h.01M15.9 10h.01M18 10h.01M7.5 14h9")]
        case .menu: [.path("M4 7h16M4 12h16M4 17h16")]
        case .star: [.path("m12 3.8 2.5 5.2 5.7.8-4.1 4 1 5.6L12 16.7l-5.1 2.7 1-5.6-4.1-4 5.7-.8z")]
        case .eye: [.path("M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"), .circle(12, 12, 2.8)]
        case .book: [.path("M12 6.5C10 5 7 4.5 3.5 5v13.5C7 18 10 18.5 12 20c2-1.5 5-2 8.5-1.5V5C17 4.5 14 5 12 6.5z"
                           + "M12 6.5V20")]
        case .phone: [.rect(6.5, 2.5, 11, 19, 2.5), .path("M11 18.5h2")]
        case .tag: [.path("M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3a1.5 1.5 0 0 1 0 2.1l-6.3 6.3a1.5 1.5 0 0 1-2.1 0z"),
                    .circle(8, 8, 1.3)]
        case .mail: [.rect(3, 5.5, 18, 13, 2), .path("m3.5 7 8.5 6 8.5-6")]
        case .plus: [.path("M12 5v14M5 12h14")]
        case .user: [.circle(12, 8.5, 3.8), .path("M4.5 20c1.2-3.6 4-5.3 7.5-5.3s6.3 1.7 7.5 5.3")]
        case .ban: [.circle(12, 12, 8.5), .path("m6 6 12 12")]
        case .bell: [.path("M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z"), .path("M10 20.5a2 2 0 0 0 4 0")]
        case .external: [.path("M14 4.5h5.5V10M19.5 4.5 11 13M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5"
                               + "v-11A1.5 1.5 0 0 1 5.5 6H10")]
        case .expand: [.path("M14 4h6v6M20 4l-6.5 6.5M10 20H4v-6M4 20l6.5-6.5")]
        case .shrink: [.path("M4 14h6v6M10 14l-6.5 6.5M20 10h-6V4M14 10l6.5-6.5")]
        case .aa: [.path("M2.5 18.5 7.5 5.5l5 13M4.4 14h6.2"), .circle(17.2, 15, 3.3), .path("M20.5 11.5v7")]
        case .restore: [.path("M4 12a8 8 0 1 0 2.4-5.7L4 8.6"), .path("M4 4v4.6h4.6")]
        case .sidebar: [.rect(3.5, 4.5, 17, 15, 2), .path("M9.5 4.5v15")]

        case .bellOff: [.path("M6 16.5V11a6 6 0 0 1 9.4-4.9M18 11v5.5l1.5 2H9"),
                        .path("M10 20.5a2 2 0 0 0 4 0M4 4l16 16")]
        case .pin: [.path("M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"), .circle(12, 10, 2.3)]
        case .download: [.path("M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14")]
        case .file: [.path("M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"),
                     .path("M14 3v5h5M9 13h6M9 16.5h4")]
        case .image: [.rect(3.5, 4.5, 17, 15, 2), .circle(9, 10, 1.6), .path("m20.5 16-4.5-4.5L6 19.5")]
        case .table: [.rect(3.5, 4.5, 17, 15, 2), .path("M3.5 10h17M3.5 15h17M10 4.5v15")]
        case .slides: [.rect(3, 4, 18, 12, 1.8), .path("M12 16v4M8 20.5h8")]
        case .lock: [.rect(5, 10.5, 14, 10, 2), .path("M8 10.5V7.5a4 4 0 0 1 8 0v3")]
        case .key: [.circle(8, 15, 4), .path("m11 12 8.5-8.5M16.5 6.5 19 9M14.5 8.5l2 2")]
        case .chat: [.path("M20.5 11.5a7.5 7.5 0 0 1-11 6.6L4 19.5l1.4-4.8a7.5 7.5 0 1 1 15.1-3.2z")]
        case .list: [.path("M9.5 7h10.5M9.5 12h10.5M9.5 17h10.5M4.5 7h.01M4.5 12h.01M4.5 17h.01")]
        case .todo: [.path("M11 7h9M11 12h9M11 17h9"),
                     .path("m3.5 7 1.6 1.6L8 5.7M3.5 12l1.6 1.6L8 10.7M3.5 17l1.6 1.6L8 15.7")]

        // A cog of eight teeth, as wide as the clock, round a hub.
        case .gear: [.path("M10.3 5.6 10.7 3.1A9 9 0 0 1 13.3 3.1L13.7 5.6A6.6 6.6 0 0 1 15.3 6.3L17.4 4.8"
                           + "A9 9 0 0 1 19.2 6.6L17.7 8.7A6.6 6.6 0 0 1 18.4 10.3L20.9 10.7A9 9 0 0 1 20.9 13.3"
                           + "L18.4 13.7A6.6 6.6 0 0 1 17.7 15.3L19.2 17.4A9 9 0 0 1 17.4 19.2L15.3 17.7"
                           + "A6.6 6.6 0 0 1 13.7 18.4L13.3 20.9A9 9 0 0 1 10.7 20.9L10.3 18.4A6.6 6.6 0 0 1 8.7 17.7"
                           + "L6.6 19.2A9 9 0 0 1 4.8 17.4L6.3 15.3A6.6 6.6 0 0 1 5.6 13.7L3.1 13.3A9 9 0 0 1 3.1 10.7"
                           + "L5.6 10.3A6.6 6.6 0 0 1 6.3 8.7L4.8 6.6A9 9 0 0 1 6.6 4.8L8.7 6.3"
                           + "A6.6 6.6 0 0 1 10.3 5.6z"),
                     .circle(12, 12, 2.8)]
        case .at: [.circle(12, 12, 3.5), .path("M15.5 8.5v4.75a2.25 2.25 0 0 0 4.5 0V12a8 8 0 1 0-2.9 6.2")]
        case .info: [.circle(12, 12, 8.5), .path("M12 11v5.5M12 7.8v.2")]
        case .question: [.circle(12, 12, 8.5),
                         .path("M9.6 9.6a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1.1.9-1.1 1.6v.3M12 16.8v.2")]
        case .minus: [.path("M5 12h14")]
        case .copy: [.rect(8.5, 8.5, 12, 12, 2), .path("M15.5 8.5v-3a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3")]
        case .link: [.path("M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.2 1.2"),
                     .path("M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.2-1.2")]
        case .filter: [.path("M4 7h16M7 12h10M10 17h4")]
        case .calAdd: [.rect(3.5, 5, 17, 15.5, 2), .path("M3.5 9.5h17M8 3v4M16 3v4M12 12.5v5M9.5 15h5")]
        case .unlock: [.rect(5, 10.5, 14, 10, 2), .path("M8 10.5V7.5a4 4 0 0 1 7.8-1.2")]
        case .circle: [.circle(12, 12, 8.5)]
        case .checkCircle: [.circle(12, 12, 8.5), .path("m8.5 12.3 2.4 2.4 4.6-4.9")]
        case .mailOpen: [.path("M3 10.2 12 4.5l9 5.7V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"),
                         .path("m3.5 10.5 8.5 5.8 8.5-5.8")]
        case .outbox: [.path("M3.5 13.5h4.6l1.6 2.5h4.6l1.6-2.5h4.6V18a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"),
                       .path("M12 11V3.5M8.5 7 12 3.5 15.5 7")]
        case .command: [.path("M9 9V6.5A2.5 2.5 0 1 0 6.5 9h11A2.5 2.5 0 1 0 15 6.5v11a2.5 2.5 0 1 0 2.5-2.5h-11"
                              + "A2.5 2.5 0 1 0 9 17.5z")]
        case .arrowRight: [.path("M4.5 12h15M13.5 6l6 6-6 6")]
        }
    }

    private static let drawn: [MeadowIcon: Path] = Dictionary(uniqueKeysWithValues: allCases.map { icon in
        // Every icon's data is read by the tests, so one that can't be read is caught there, not drawn half here.
        (icon, (try? SVGPath.path(icon.parts)) ?? Path())
    })

    /// The icon on its 24-point grid, read once.
    var path: Path { Self.drawn[self] ?? Path() }

    /// The icon scaled from its grid to a square of `size` points.
    func path(size: CGFloat) -> Path {
        path.applying(CGAffineTransform(scaleX: size / 24, y: size / 24))
    }

    /// The nearest SF Symbol, for a place that can take only a symbol's name.
    var stand: String {
        switch self {
        case .sun: "sun.max"
        case .moon: "moon"
        case .dusk: "sun.horizon"
        case .inbox: "tray"
        case .gate: "door.left.hand.closed"
        case .hourglass: "hourglass"
        case .bookmark: "bookmark"
        case .clock: "clock"
        case .leaf: "leaf"
        case .receipt: "doc.text"
        case .clip: "paperclip"
        case .folder: "folder"
        case .search: "magnifyingglass"
        case .pen: "pencil"
        case .archive: "archivebox"
        case .trash: "trash"
        case .reply: "arrowshape.turn.up.left"
        case .replyAll: "arrowshape.turn.up.left.2"
        case .forward: "arrowshape.turn.up.right"
        case .more: "ellipsis"
        case .check: "checkmark"
        case .x: "xmark"
        case .down: "chevron.down"
        case .up: "chevron.up"
        case .left: "chevron.left"
        case .right: "chevron.right"
        case .wind: "wind"
        case .shield: "checkmark.shield"
        case .alert: "exclamationmark.triangle"
        case .cal: "calendar"
        case .sliders: "slider.horizontal.3"
        case .undo: "arrow.uturn.backward"
        case .send: "paperplane"
        case .cloudOff: "icloud.slash"
        case .refresh: "arrow.clockwise"
        case .keyboard: "keyboard"
        case .menu: "line.3.horizontal"
        case .star: "star"
        case .eye: "eye"
        case .book: "book"
        case .phone: "iphone"
        case .tag: "tag"
        case .mail: "envelope"
        case .plus: "plus"
        case .user: "person"
        case .ban: "nosign"
        case .bell: "bell"
        case .external: "arrow.up.forward.square"
        case .expand: "arrow.up.left.and.arrow.down.right"
        case .shrink: "arrow.down.right.and.arrow.up.left"
        case .aa: "textformat.size"
        case .restore: "arrow.counterclockwise"
        case .sidebar: "sidebar.left"
        case .bellOff: "bell.slash"
        case .pin: "mappin"
        case .download: "arrow.down.to.line"
        case .file: "doc"
        case .image: "photo"
        case .table: "tablecells"
        case .slides: "rectangle.on.rectangle"
        case .lock: "lock"
        case .key: "key"
        case .chat: "bubble.left"
        case .list: "list.bullet"
        case .todo: "checklist"
        case .gear: "gearshape"
        case .at: "at"
        case .info: "info.circle"
        case .question: "questionmark.circle"
        case .minus: "minus"
        case .copy: "doc.on.doc"
        case .link: "link"
        case .filter: "line.3.horizontal.decrease"
        case .calAdd: "calendar.badge.plus"
        case .unlock: "lock.open"
        case .circle: "circle"
        case .checkCircle: "checkmark.circle"
        case .mailOpen: "envelope.open"
        case .outbox: "tray.and.arrow.up"
        case .command: "command"
        case .arrowRight: "arrow.right"
        }
    }
}

/// An icon as a shape on its 24-point grid, scaled to the rect it is given and centred there.
struct MeadowIconShape: Shape {
    let icon: MeadowIcon

    init(_ icon: MeadowIcon) {
        self.icon = icon
    }

    func path(in rect: CGRect) -> Path {
        let side = min(rect.width, rect.height)
        return icon.path(size: side).offsetBy(dx: rect.midX - side / 2, dy: rect.midY - side / 2)
    }
}

extension MeadowIcon {
    /// The stroke the mockup draws every icon with, on the 24-point grid: `.ic`'s 1.75.
    static let stroke: CGFloat = 1.75

    /// The stroke's style at a size: SVG scales the stroke with the symbol, so it keeps its weight on the grid.
    static func strokeStyle(size: CGFloat, stroke: CGFloat = MeadowIcon.stroke) -> StrokeStyle {
        StrokeStyle(lineWidth: stroke * size / 24, lineCap: .round, lineJoin: .round)
    }
}

/// An icon at a size in points (the mockup's 18, 15 small, 22 large), stroked in the foreground style.
struct MeadowIconView: View {
    let icon: MeadowIcon
    var size: CGFloat = 18
    /// The stroke on the 24-point grid: 1.75, or the heavier 2.6 the list's check box draws its tick with.
    var stroke: CGFloat = MeadowIcon.stroke

    init(_ icon: MeadowIcon, size: CGFloat = 18) {
        self.icon = icon
        self.size = size
    }

    init(_ icon: MeadowIcon, size: CGFloat = 18, stroke: CGFloat) {
        self.icon = icon
        self.size = size
        self.stroke = stroke
    }

    var body: some View {
        MeadowIconShape(icon)
            .stroke(style: MeadowIcon.strokeStyle(size: size, stroke: stroke))
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

#if os(macOS)
extension MeadowIcon {
    /// The icon at a size, stroked in a colour, for AppKit and the rows Core Text draws. A dynamic colour is resolved
    /// each time the image draws, in the appearance it draws in.
    func image(size: CGFloat, color: NSColor, stroke: CGFloat = MeadowIcon.stroke) -> NSImage {
        let path = path(size: size).cgPath
        let style = Self.strokeStyle(size: size, stroke: stroke)
        let image = NSImage(size: NSSize(width: size, height: size), flipped: true) { _ in
            guard let context = NSGraphicsContext.current?.cgContext else { return false }
            context.addPath(path)
            context.setStrokeColor(color.cgColor)
            context.setLineWidth(style.lineWidth)
            context.setLineCap(.round)
            context.setLineJoin(.round)
            context.strokePath()
            return true
        }
        return image
    }

    /// The icon as a template, which a menu or a control tints as its text: black on clear, as AppKit asks.
    func template(size: CGFloat = 16, stroke: CGFloat = MeadowIcon.stroke) -> NSImage {
        let image = image(size: size, color: .black, stroke: stroke)
        image.isTemplate = true
        return image
    }
}

extension Image {
    /// A Meadow icon where SwiftUI asks for an `Image`, as a menu's item or a `Label`'s icon does: a template, so it
    /// takes the foreground style.
    init(meadow icon: MeadowIcon, size: CGFloat = 16) {
        self.init(nsImage: icon.template(size: size))
    }
}
#endif
