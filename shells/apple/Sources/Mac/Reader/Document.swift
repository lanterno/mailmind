#if os(macOS)
import Observation
import SwiftUI

// The reading pane's document (#44), as the mockup's `.pdoc` lays it out under the pane's bar: the chips and the
// subject, then the aside (what MailMind read of the message, and cards about it) and the message itself. Narrower
// than 1100 the three stand in one column, the aside above the message; at 1100 and wider the message keeps the
// reading measure and the aside stands beside it, 300 wide, held in view as the message scrolls.

/// Where the document stands in a pane of `width`, its message at most `measure` wide (the mockup's container rules).
struct PaneLayout: Equatable {
    /// From this wide the aside stands beside the message.
    static let twoColumnsFrom: CGFloat = 1100
    /// Up to this wide the document's margins are a phone's.
    static let tightUpTo: CGFloat = 700
    static let aside: CGFloat = 300
    static let gap: CGFloat = 44
    /// How far below the pane's top the aside is held as the message scrolls (`top: 18px`).
    static let held: CGFloat = 18

    var width: CGFloat
    var measure: CGFloat

    var twoColumns: Bool { width >= Self.twoColumnsFrom }
    var tight: Bool { width <= Self.tightUpTo }

    var padding: EdgeInsets {
        tight ? EdgeInsets(top: 18, leading: 20, bottom: 80, trailing: 20)
            : EdgeInsets(top: 26, leading: 48, bottom: 96, trailing: 48)
    }

    /// The widest the document grows, its margins in it (`max-width`): the measure and the room around it, and the
    /// aside's beside it when there are two columns.
    var widest: CGFloat {
        (twoColumns ? measure + Self.aside + Self.gap : measure) + 96
    }

    /// The width inside the margins.
    var content: CGFloat {
        max(0, min(width, widest) - padding.leading - padding.trailing)
    }

    /// The message's column: the whole of the content in one column, the room the aside leaves in two.
    var main: CGFloat {
        twoColumns ? max(0, content - Self.aside - Self.gap) : content
    }

    /// How far the aside moves down to stay `held` below the top, once the document has scrolled `scrolled` past it,
    /// never past the foot of the row it stands in.
    static func stuck(scrolled: CGFloat, rowTop: CGFloat, rowHeight: CGFloat, asideHeight: CGFloat) -> CGFloat {
        min(max(0, scrolled + held - rowTop), max(0, rowHeight - asideHeight))
    }
}

extension ReadingPrefs {
    /// The reading measure the settings give.
    var measure: CGFloat { MailLook.measure(width, size: look.size) }
}

// MARK: The chips

/// One chip over the subject (`.chips`).
struct DocumentChip: Equatable, Identifiable {
    enum Kind: Equatable {
        /// The account the mail came to, with its colour's place in the config's order.
        case account(Int)
        /// A folder of the person's own.
        case folder
        /// It needs you, in sunlight.
        case needs
        /// Where it lies out of the Inbox: Archive, Trash, Sent.
        case place
        case replied
    }

    let kind: Kind
    let text: String
    var id: String { "\(kind)\(text)" }

    /// A folder's role as the rail names it: the Inbox by its name, and a folder the rail doesn't know as the
    /// person's own.
    static func role(of folder: String, rail: [API.RailFolder]) -> String {
        rail.first { $0.name == folder }?.role
            ?? (folder.caseInsensitiveCompare("INBOX") == .orderedSame ? "inbox" : "custom")
    }

    /// The chips for a message, in the mockup's order: its account, the person's folder it is in, whether it needs
    /// you, where it lies when that is out of the Inbox, and whether it was answered. A folder the rail doesn't know
    /// is named as the person's.
    static func of(account: String, accountIndex: Int, folder: String, rail: [API.RailFolder], needsYou: Bool,
                   replied: Bool) -> [DocumentChip] {
        var chips = [DocumentChip(kind: .account(accountIndex), text: account)]
        let known = rail.first { $0.name == folder }
        let role = role(of: folder, rail: rail)
        if role == "custom" { chips.append(DocumentChip(kind: .folder, text: known.map(Words.role) ?? folder)) }
        if needsYou { chips.append(DocumentChip(kind: .needs, text: String(localized: "Needs you"))) }
        if role != "inbox" && role != "custom", let known {
            chips.append(DocumentChip(kind: .place, text: String(localized: "In \(Words.role(known))")))
        }
        if replied { chips.append(DocumentChip(kind: .replied, text: String(localized: "Replied"))) }
        return chips
    }
}

/// Where a message stands in the window, as its document says it: its chips, its folder's role and name, the
/// account it came to and that account's colour, and the mailbox's ways to the sender. With no window (a test's
/// reader), only what the copy itself says.
@MainActor
struct DocumentPlace {
    var chips: [DocumentChip]
    var role: String
    /// The folder, as its chip or the rail names it.
    var filed: String
    /// The account's name, and its colour's place among the accounts.
    var me: String
    var accountIndex: Int
    var lane: Lane?
    var write: ((String) -> Void)?
    var allMail: ((String) -> Void)?

    /// Whether the reply box stands under the message: as the mockup has it, in the Inbox, Sent and the person's
    /// own folders, not where mail is put away or thrown out.
    var offersReply: Bool { !["trash", "junk", "drafts", "archive", "all"].contains(role) }

    /// `mailbox` names the accounts and folders where there is no window around the document (a message's own
    /// window), so it says them as the mail window does.
    static func of(app: MailApp?, mailbox: Mailbox? = nil, copy: MailCopy, needsYou: Bool,
                   replied: Bool) -> DocumentPlace {
        let mailbox = mailbox ?? app?.mailbox
        let rail = mailbox?.folders(of: copy.account) ?? []
        let me = mailbox?.name(of: copy.account) ?? Words.short(copy.account)
        let index = mailbox?.colourPlace(of: copy.account) ?? 0
        let role = DocumentChip.role(of: copy.folder, rail: rail)
        let filed = rail.first { $0.name == copy.folder }.map(Words.role) ?? copy.folder
        var lane: Lane?
        if case .search(let search)? = app?.place { lane = search.lane }
        return DocumentPlace(
            chips: DocumentChip.of(account: me, accountIndex: index, folder: copy.folder, rail: rail,
                                   needsYou: needsYou, replied: replied),
            role: role, filed: filed, me: me, accountIndex: index, lane: lane,
            write: mailbox?.composeTo,
            allMail: app.map { app in { [weak app] address in app?.place = .search(Search(q: "from:\(address)")) } })
    }
}

/// The chips and the subject (`.phead`).
struct DocumentHead: View {
    let chips: [DocumentChip]
    let subject: String
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            FlowLayout(spacing: 6) {
                ForEach(chips) { chip in
                    switch chip.kind {
                    case .account(let index): MeadowChip(chip.text, tone: .account(meadow.account(index)))
                    case .folder: MeadowChip(chip.text, icon: .folder)
                    case .needs: MeadowChip(chip.text, tone: .sun)
                    case .place: MeadowChip(chip.text, tone: .line)
                    case .replied: MeadowChip(chip.text, icon: .reply)
                    }
                }
            }
            Text(verbatim: subject)
                .font(.meadow(.display, 28, .semibold))
                .lineSpacing(MeadowFace.display.lineSpacing(size: 28, lineHeight: 1.28, weight: .semibold))
                .foregroundStyle(meadow.ink)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
                .accessibilityAddTraits(.isHeader)
                .accessibilityIdentifier("reader-subject")
                .padding(.top, 10)
                .padding(.bottom, 18)
        }
    }
}

// MARK: The document

/// How far the document has scrolled, and where the row the aside stands in is: only the aside reads it, so a scroll
/// moves the aside and redraws nothing else.
@MainActor
@Observable
final class DocumentScroll {
    var offset: CGFloat = 0
    var row = CGRect.zero
}

/// The document: the head, then the aside and the message in one column or two, in a pane as wide as the frame says.
struct PaneDocument<Aside: View, Main: View>: View {
    let chips: [DocumentChip]
    let subject: String
    let measure: CGFloat
    @ViewBuilder let aside: Aside
    @ViewBuilder let main: Main
    @Environment(\.meadowPaneWidth) private var width
    @State private var scroll = DocumentScroll()
    /// Whether the aside has anything in it: an empty one leaves no gap above the message.
    @State private var asideShown = false

    var body: some View {
        let layout = PaneLayout(width: width, measure: measure)
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                DocumentHead(chips: chips, subject: subject)
                if layout.twoColumns {
                    HStack(alignment: .top, spacing: PaneLayout.gap) {
                        VStack(alignment: .leading, spacing: 0) { main }
                            .frame(width: layout.main, alignment: .leading)
                        HeldAside(scroll: scroll) {
                            VStack(alignment: .leading, spacing: 14) { aside }
                        }
                        .frame(width: PaneLayout.aside, alignment: .top)
                    }
                    .onGeometryChange(for: CGRect.self) { $0.frame(in: .named(documentSpace)) } action: {
                        scroll.row = $0
                    }
                } else {
                    VStack(alignment: .leading, spacing: 14) { aside }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .onGeometryChange(for: Bool.self) { $0.size.height > 1 } action: { asideShown = $0 }
                        .padding(.bottom, asideShown ? 20 : 0)
                    VStack(alignment: .leading, spacing: 0) { main }
                }
            }
            .frame(width: layout.content, alignment: .leading)
            .padding(layout.padding)
            .frame(maxWidth: .infinity)
            .coordinateSpace(.named(documentSpace))
        }
        .onScrollGeometryChange(for: CGFloat.self) { $0.contentOffset.y + $0.contentInsets.top } action: { _, y in
            scroll.offset = y
        }
    }
}

/// The document's own coordinates, where the aside finds the row it stands in.
private let documentSpace = "pane-document"

/// A button that reads as a link (`.linkbtn`): underlined words in the primary's ink, paper under them on a hover.
struct ReaderLinkStyle: ButtonStyle {
    var size: CGFloat = 12.5

    func makeBody(configuration: Configuration) -> some View {
        ReaderLink(label: configuration.label, size: size)
    }
}

private struct ReaderLink: View {
    let label: ButtonStyleConfiguration.Label
    let size: CGFloat
    @Environment(\.meadow) private var meadow
    @Environment(\.isFocused) private var focused
    @State private var hovering = false

    var body: some View {
        label
            .font(.meadow(.ui, size, .bold))
            .underline(true, color: meadow.primaryInk)
            .foregroundStyle(meadow.primaryInk)
            .padding(.horizontal, 4)
            .padding(.vertical, 2)
            .background(hovering ? meadow.paper3 : .clear, in: RoundedRectangle(cornerRadius: 6))
            .contentShape(Rectangle())
            .onHover { hovering = $0 }
            .meadowFocusRing(focused)
    }
}

/// The aside beside the message, held `PaneLayout.held` below the pane's top while the message scrolls past it.
private struct HeldAside<Content: View>: View {
    let scroll: DocumentScroll
    @ViewBuilder let content: Content
    @State private var height: CGFloat = 0

    var body: some View {
        content
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height = $0 }
            .offset(y: PaneLayout.stuck(scrolled: scroll.offset, rowTop: scroll.row.minY, rowHeight: scroll.row.height,
                                        asideHeight: height))
    }
}
#endif
