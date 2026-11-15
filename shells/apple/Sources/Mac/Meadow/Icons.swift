import SwiftUI

// Meadow's icons (#44): one stroke family on a 24-point grid, the mockup's own. Until their paths are drawn here, each
// shows the nearest SF Symbol.

/// Every icon Meadow draws, named as the mockup names its symbols.
enum MeadowIcon: String, CaseIterable, Sendable {
    case sun, moon, dusk, inbox, gate, hourglass, bookmark, clock, leaf, receipt, clip, folder, search, pen, archive
    case trash, reply, replyAll = "replyall", forward, more, check, x, down, up, left, right, wind, shield, alert, cal
    case sliders, undo, send, cloudOff = "cloud-off", refresh, keyboard, menu, star, eye, book, phone, tag, mail, plus
    case user, ban, bell, external, expand, shrink, aa, restore, sidebar

    /// The SF Symbol shown until the icon's own paths are drawn.
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
        }
    }
}

/// An icon at a size in points (the mockup's 18, 15 small, 22 large), stroked in the foreground style.
struct MeadowIconView: View {
    let icon: MeadowIcon
    var size: CGFloat = 18

    init(_ icon: MeadowIcon, size: CGFloat = 18) {
        self.icon = icon
        self.size = size
    }

    var body: some View {
        Image(systemName: icon.stand)
            .resizable()
            .scaledToFit()
            .fontWeight(.medium)
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}
