#if os(macOS)
import AppKit
import SwiftUI

// The list's rows as Meadow draws them (#44), the mockup's `.row`: each drawn into one layer with Core Text, as the
// step-0 list's are, so a row costs no views of its own. The colours, faces, avatars' colours and icons are made once
// for each light and shared by every row (`RowInks`).

/// What a row offers the pointer: one of its quiet buttons at the top right, or the check box over its avatar.
enum RowTarget: Equatable {
    case act(RowAct)
    case check
}

/// A row's quiet buttons (`.row-acts`): file, delete and snooze; in Trash, restore; in Archive, back to the Inbox.
enum RowAct: Equatable {
    case file, toInbox, delete, snooze, restore

    /// The buttons a row in a folder of this part shows.
    static func shown(role: String?) -> [RowAct] {
        switch role {
        case "trash": [.restore]
        case "archive", "all": [.toInbox, .delete, .snooze]
        default: [.file, .delete, .snooze]
        }
    }

    var icon: MeadowIcon {
        switch self {
        case .file: .archive
        case .toInbox: .inbox
        case .delete: .trash
        case .snooze: .clock
        case .restore: .restore
        }
    }

    /// Its name, with the key that does the same.
    var title: String {
        switch self {
        case .file: String(localized: "File to Archive · E")
        case .toInbox: String(localized: "Move to Inbox")
        case .delete: String(localized: "Delete · D")
        case .snooze: String(localized: "Snooze · H")
        case .restore: String(localized: "Restore to Inbox")
        }
    }
}

/// The colours and pictures the rows draw with in one light, made once and kept: AppKit's copies of the palette's
/// colours, each name's avatar colours, the icons in the inks they are drawn in, and the brief's little scene.
@MainActor
final class RowInks {
    let light: MeadowLight
    let paper, paper2, paper3, paperRaised, line2: NSColor
    let ink, ink2, ink3: NSColor
    let primary, primaryWash, onPrimary: NSColor
    let sun, sunInk, sunWash, moss, rust, rustWash, focus: NSColor
    /// A row checked among several (`.row.checked`): the primary's wash, a little paper in it.
    let checked: NSColor
    let accounts: [NSColor]
    private var avatars: [Int: (fill: NSColor, ink: NSColor)] = [:]
    private var icons: [String: NSImage] = [:]
    private let palette: MeadowPalette
    private(set) lazy var briefScene: NSImage = Self.scene(palette, light: light)

    private static var made: [String: RowInks] = [:]

    static func of(_ palette: MeadowPalette, light: MeadowLight, highContrast: Bool) -> RowInks {
        let key = "\(light.rawValue)\(highContrast)"
        if let held = made[key] { return held }
        let inks = RowInks(palette, light: light)
        made[key] = inks
        return inks
    }

    private init(_ p: MeadowPalette, light: MeadowLight) {
        self.light = light
        palette = p
        (paper, paper2, paper3, paperRaised, line2) = (NSColor(p.paper), NSColor(p.paper2), NSColor(p.paper3),
                                                       NSColor(p.paperRaised), NSColor(p.line2))
        (ink, ink2, ink3) = (NSColor(p.ink), NSColor(p.ink2), NSColor(p.ink3))
        (primary, primaryWash, onPrimary) = (NSColor(p.primary), NSColor(p.primaryWash), NSColor(p.onPrimary))
        (sun, sunInk, sunWash, moss) = (NSColor(p.sun), NSColor(p.sunInk), NSColor(p.sunWash), NSColor(p.moss))
        (rust, rustWash, focus) = (NSColor(p.rust), NSColor(p.rustWash), NSColor(p.focus))
        checked = NSColor(p.primaryWash.meadowMix(0.7, with: p.paper2))
        accounts = p.accounts.map { NSColor($0) }
    }

    func account(_ index: Int) -> NSColor {
        accounts.isEmpty ? moss : accounts[((index % accounts.count) + accounts.count) % accounts.count]
    }

    /// A name's avatar colours (`MeadowHue`, `MeadowAvatar.colours`), worked out once for each hue.
    func avatar(_ name: String) -> (fill: NSColor, ink: NSColor) {
        let hue = Int(MeadowHue.of(name))
        if let held = avatars[hue] { return held }
        let (fill, ink) = MeadowAvatar.colours(Double(hue), in: light)
        let made = (NSColor(fill), NSColor(ink))
        avatars[hue] = made
        return made
    }

    /// An icon in a colour, drawn once.
    func icon(_ icon: MeadowIcon, size: CGFloat, color: NSColor, stroke: CGFloat = MeadowIcon.stroke) -> NSImage {
        let key = "\(icon.rawValue) \(size) \(stroke) \(color.hashValue)"
        if let held = icons[key] { return held }
        let made = icon.image(size: size, color: color, stroke: stroke)
        icons[key] = made
        return made
    }

    /// The brief's thumbnail (`.bthumb`): the scene of seed 4, pinned at its bottom right, drawn once for the light.
    private static func scene(_ palette: MeadowPalette, light: MeadowLight) -> NSImage {
        let side = RowMeasure.avatar
        let renderer = ImageRenderer(content: MeadowScene(seed: 4)
            .frame(width: side, height: side)
            .environment(\.meadow, palette)
            .environment(\.meadowLight, light))
        renderer.scale = 2
        return renderer.nsImage ?? NSImage(size: NSSize(width: side, height: side))
    }
}

/// The rows' faces, made once.
@MainActor
enum RowFaces {
    static let who = NSFont.meadow(.ui, 14, .semibold)
    static let whoUnread = NSFont.meadow(.ui, 14, .heavy)
    static let count = NSFont.meadow(.ui, 12, .bold)
    static let time = tabular(NSFont.meadow(.ui, 12.5))
    static let timeNeeds = tabular(NSFont.meadow(.ui, 12.5, .bold))
    static let subject = NSFont.meadow(.ui, 14)
    static let subjectUnread = NSFont.meadow(.ui, 14, .heavy)
    static let gist = NSFont.meadow(.ui, RowMeasure.gistSize, .medium)
    static let preview = NSFont.meadow(.ui, 13)
    static let amount = tabular(NSFont.meadow(.ui, 13, .bold))
    static let chip = NSFont.meadow(.ui, 11.5, .bold)
    static let initials = NSFont.meadow(.ui, 12, .heavy)
    static let head = NSFont.meadow(.ui, 11, .heavy)

    /// Figures all one width (`font-variant-numeric: tabular-nums`), so times line up.
    private static func tabular(_ font: NSFont) -> NSFont {
        let settings: [[NSFontDescriptor.FeatureKey: Int]] = [[.typeIdentifier: kNumberSpacingType,
                                                               .selectorIdentifier: kMonospacedNumbersSelector]]
        let described = font.fontDescriptor.addingAttributes([.featureSettings: settings])
        return NSFont(descriptor: described, size: font.pointSize) ?? font
    }

    /// A summary as the rows set it: on one line, in moss from the context.
    static func gistText(_ summary: String) -> NSAttributedString {
        NSAttributedString(string: TextLine.flat(summary), attributes: [
            .font: gist,
            NSAttributedString.Key(kCTForegroundColorFromContextAttributeName as String): true,
        ])
    }

    /// A summary's width on one line, which says whether it fits on one (`RowMeasure.fits`): a single line set, the
    /// least a row's height can be worked out from.
    static func gistWidth(_ summary: String) -> Float {
        Float(CTLineGetTypographicBounds(CTLineCreateWithAttributedString(gistText(summary)), nil, nil, nil))
    }

    /// The baseline of a line of `font` in a CSS line box `height` tall whose top is `top`: the font's ascent and
    /// descent stand in the middle of the box.
    static func baseline(_ font: NSFont, top: CGFloat, height: CGFloat) -> CGFloat {
        top + (height - (font.ascender - font.descender)) / 2 + font.ascender
    }
}

/// How a row stands now: chosen, checked among several, under the pointer (and on which of its parts), and whether
/// the list is checking rows, which shows every check box.
struct RowState: Equatable {
    var chosen = false
    var checked = false
    var hovering = false
    var target: RowTarget?
    var selecting = false
    /// The list has the keyboard's focus, from the keyboard: the chosen row shows the focus ring.
    var keyed = false
}

/// What every row of the list shares: its inks, how much each row shows, and how accounts are coloured.
@MainActor
struct RowLook {
    let inks: RowInks
    let measure: RowMeasure
    /// Each account's place in the mailbox's list, for its colour.
    let accounts: [String]
    /// Your addresses (`RowWords.mine`): a row of your own mail names whom it went to. Without them, the accounts'.
    let mine: Set<String>

    /// The place kept for each account's colour (`AccountPlaces`), over its place in the list.
    let places: [String: Int]

    init(inks: RowInks, measure: RowMeasure, accounts: [String], mine: Set<String>? = nil,
         places: [String: Int] = [:]) {
        self.inks = inks
        self.measure = measure
        self.accounts = accounts
        self.mine = mine ?? SendGuard.mineSet(accounts)
        self.places = places
    }

    func colour(of account: String) -> NSColor {
        inks.account(AccountPlaces.place(of: account, among: accounts, in: places))
    }
}

/// One mail row: its day's head when it starts a day, then the avatar with its account's dot (or the check box),
/// who and when with the conversation's count and the flags, the subject, the summary or the snippet or a receipt's
/// amount, the chips, and under the pointer its quiet buttons. Grey bars stand where its page hasn't come.
final class MailRowCell: NSTableCellView {
    static let identifier = NSUserInterfaceItemIdentifier("mail")

    private(set) var row: API.FolderRow?
    /// The day's head it draws over the row, which its height makes room for.
    private(set) var head: String?
    /// The account's name, said to VoiceOver where the list holds several accounts' mail: drawn, it is only a dot.
    private var account: String?
    private var acts: [RowAct] = []
    private var look: RowLook?
    var state = RowState() {
        didSet { if state != oldValue { needsDisplay = true } }
    }

    private var lines: Lines?
    private var gistCut: (width: CGFloat, lines: [CTLine])?
    /// What the row's last drawing showed of its quiet buttons and its check box: a click acts only on what a person
    /// could see, never on a button a hover state set a moment ago and the screen hasn't shown yet.
    private var drew = (acts: false, check: false)

    /// Each line's text, set once when the row is shown.
    private struct Lines {
        /// Whom the row names: the sender, or for your own mail whom it went to (`RowWords.who`).
        var named: RowWords.Who
        var who: TextLine
        var count: TextLine?
        var time: TextLine
        var subject: TextLine
        var third: RowWords.Third
        var gist: NSAttributedString?
        /// The summary's width on one line, measured as the table measures it for the row's height.
        var gistWidth: Float?
        var amount: TextLine?
        var preview: TextLine?
        var chips: [(chip: RowWords.Chip, text: TextLine)]
        var initials: TextLine
        var head: TextLine?
    }

    init() {
        super.init(frame: NSRect(x: 0, y: 0, width: 400, height: 100))
        identifier = Self.identifier
        wantsLayer = true
        layerContentsRedrawPolicy = .onSetNeedsDisplay
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not from a nib") }

    override var isFlipped: Bool { true }

    /// Show `row` (nil while its page comes) under `head`, with `acts` for its buttons; `account` is its account's
    /// name where the list says each row's.
    func show(_ row: API.FolderRow?, head: String?, account: String? = nil, acts: [RowAct], look: RowLook) {
        // The accounts' order colours the dot, and comes after the first rows at launch.
        guard row != self.row || head != self.head || account != self.account || acts != self.acts
            || look.inks !== self.look?.inks || look.measure != self.look?.measure
            || look.accounts != self.look?.accounts || lines == nil && row != nil
        else { return }
        self.row = row
        self.head = head
        self.account = account
        self.acts = acts
        self.look = look
        gistCut = nil
        lines = row.map { row in
            let unread = !row.seen
            let named = RowWords.who(row, mine: look.mine)
            let third = RowWords.third(row)
            var gist: NSAttributedString?, gistWidth: Float?, amount: TextLine?, preview: TextLine?
            switch third {
            case .gist(let text):
                gist = RowFaces.gistText(text)
                gistWidth = RowFaces.gistWidth(text)
            case .amount(let money, let snippet):
                amount = TextLine(money, RowFaces.amount)
                preview = TextLine(" · " + snippet, RowFaces.preview)
            case .preview(let snippet):
                preview = TextLine(snippet, RowFaces.preview)
            }
            return Lines(
                named: named,
                who: TextLine(named.name, unread ? RowFaces.whoUnread : RowFaces.who),
                count: row.conversationCount.map { TextLine(String($0), RowFaces.count) },
                time: TextLine(row.when, RowWords.needs(row) ? RowFaces.timeNeeds : RowFaces.time),
                subject: TextLine(row.subject.isEmpty ? String(localized: "(no subject)") : row.subject,
                                  unread ? RowFaces.subjectUnread : RowFaces.subject),
                third: third, gist: gist, gistWidth: gistWidth, amount: amount, preview: preview,
                chips: RowWords.chips(row).map { ($0, TextLine($0.text, RowFaces.chip)) },
                initials: TextLine(RowWords.initials(name: named.avatarName, address: named.avatarAddress),
                                   RowFaces.initials, kern: 0.24),
                head: head.map { TextLine($0.uppercased(), RowFaces.head, kern: 1.1) })
        }
        needsDisplay = true
    }

    // MARK: Where things stand

    private var headHeight: CGFloat { head != nil && row != nil ? RowMeasure.head : 0 }

    /// The row's own box, inside the list's sides and under its day's head.
    private var box: NSRect {
        NSRect(x: RowMeasure.listSide, y: headHeight, width: bounds.width - 2 * RowMeasure.listSide,
               height: bounds.height - headHeight)
    }

    private var avatar: NSRect {
        let measure = look?.measure ?? RowMeasure(.comfortable)
        return NSRect(x: box.minX + RowMeasure.side, y: box.minY + measure.padTop, width: RowMeasure.avatar,
                      height: RowMeasure.avatar)
    }

    /// The quiet buttons' strip (`.row-acts`): 6 in from the box's top right, 28 a button, 1 apart, with 10 of the
    /// row's own colour before them over whatever they cover.
    private var actsStrip: NSRect {
        let width = 10 + CGFloat(acts.count) * 28 + CGFloat(max(acts.count - 1, 0)) + 2
        return NSRect(x: box.maxX - 6 - width, y: box.minY + 6, width: width, height: 32)
    }

    private func actFrame(_ index: Int) -> NSRect {
        NSRect(x: actsStrip.minX + 10 + CGFloat(index) * 29, y: actsStrip.minY + 2, width: 28, height: 28)
    }

    /// What of the row is at `point`, in the cell's own coordinates, drawn or not: where the pointer is.
    func target(at point: NSPoint) -> RowTarget? {
        guard row != nil else { return nil }
        let point = mirrored(point)
        for (index, act) in acts.enumerated() where actFrame(index).contains(point) {
            return .act(act)
        }
        if avatar.contains(point) { return .check }
        return nil
    }

    /// What a click at `point` presses: a quiet button or the check box only where the row last drew it. Anywhere
    /// else, the click chooses the row.
    func shownTarget(at point: NSPoint) -> RowTarget? {
        switch target(at: point) {
        case .act(let act)?: drew.acts ? .act(act) : nil
        case .check?: drew.check ? .check : nil
        case nil: nil
        }
    }

    /// Where a quiet button stands in the cell, if the row has it.
    func frame(of act: RowAct) -> NSRect? {
        acts.firstIndex(of: act).map { mirrored(actFrame($0)) }
    }

    /// Whether `point` is in the day's head over the row, which chooses nothing.
    func inHead(_ point: NSPoint) -> Bool {
        point.y < headHeight
    }

    private var rtl: Bool { userInterfaceLayoutDirection == .rightToLeft }

    /// A point or a frame worked out left to right, mirrored for a right-to-left language.
    private func mirrored(_ point: NSPoint) -> NSPoint {
        rtl ? NSPoint(x: bounds.width - point.x, y: point.y) : point
    }

    private func mirrored(_ rect: NSRect) -> NSRect {
        rtl ? NSRect(x: bounds.width - rect.maxX, y: rect.minY, width: rect.width, height: rect.height) : rect
    }

    // MARK: Drawing

    override func draw(_ dirtyRect: NSRect) {
        guard let look, let context = NSGraphicsContext.current?.cgContext else { return }
        let inks = look.inks
        let measure = look.measure
        // Core Text draws upward from a baseline; the view is flipped, so the glyphs are flipped back.
        context.textMatrix = CGAffineTransform(scaleX: 1, y: -1)

        guard let row, let lines else {
            drew = (false, false)
            drawWaiting(inks, measure: measure)
            return
        }
        // The shape the table gave the row its height by: the summary on one line where it fits this wide.
        let shape = RowShape(head: head != nil, gist: lines.gist != nil, chips: !lines.chips.isEmpty,
                             bare: lines.third.isBare)
            .fitting(lines.gistWidth, in: bounds.width)
        let stops = measure.stops(shape)
        if let head = lines.head {
            head.draw(in: context, x: RowMeasure.listSide + RowMeasure.side,
                      baseline: RowFaces.baseline(RowFaces.head, top: 14, height: 11), width: box.width - 20,
                      color: inks.ink3, mirrored: mirrored)
        }

        let fill = background(inks)
        if let fill {
            fill.setFill()
            NSBezierPath(roundedRect: mirrored(box), xRadius: 12, yRadius: 12).fill()
        }
        if state.chosen, state.keyed {
            inks.focus.setStroke()
            let ring = NSBezierPath(roundedRect: mirrored(box).insetBy(dx: 1, dy: 1), xRadius: 11, yRadius: 11)
            ring.lineWidth = 2
            ring.stroke()
        }
        if !row.seen {
            inks.primary.setFill()
            NSBezierPath(ovalIn: mirrored(NSRect(x: box.minX + 2, y: box.minY + 22, width: 6, height: 6))).fill()
        }
        drawAvatar(row, lines.initials, of: lines.named.avatarKey, look: look, ring: fill ?? inks.paper, in: context)

        let textX = box.minX + RowMeasure.textX
        let right = box.maxX - RowMeasure.side
        let width = max(0, right - textX)

        // Who, the conversation's count and the flags; when, from the right.
        let whoBaseline = RowFaces.baseline(RowFaces.who, top: stops.who, height: RowMeasure.line)
        let timeColor = RowWords.needs(row) ? inks.sunInk : inks.ink3
        lines.time.draw(in: context, x: right - lines.time.width, baseline: whoBaseline, width: lines.time.width,
                        color: timeColor, mirrored: mirrored)
        var flags: [(MeadowIcon, NSColor)] = []
        if row.flagged { flags.append((.star, inks.sun)) }
        if row.hasAttach { flags.append((.clip, inks.ink3)) }
        let countRoom = lines.count.map { $0.width + 6 } ?? 0
        let flagsRoom = flags.isEmpty ? 0 : 6 + CGFloat(flags.count) * 14 + CGFloat(flags.count - 1) * 4
        let whoRoom = max(0, right - lines.time.width - 8 - textX - countRoom - flagsRoom)
        lines.who.draw(in: context, x: textX, baseline: whoBaseline, width: whoRoom, color: inks.ink,
                       mirrored: mirrored)
        var x = textX + min(lines.who.width, whoRoom)
        if let count = lines.count {
            count.draw(in: context, x: x + 6, baseline: whoBaseline, width: count.width, color: inks.ink3,
                       mirrored: mirrored)
            x += 6 + count.width
        }
        if !flags.isEmpty { x += 6 }
        for (icon, color) in flags {
            let frame = mirrored(NSRect(x: x, y: whoBaseline - 12, width: 14, height: 14))
            if icon == .star {
                drawStar(in: frame, color: color, context: context)
            } else {
                inks.icon(icon, size: 14, color: color).draw(in: frame)
            }
            x += 18
        }

        let subjectBaseline = RowFaces.baseline(RowFaces.subject, top: stops.subject, height: RowMeasure.line)
        lines.subject.draw(in: context, x: textX, baseline: subjectBaseline, width: width, color: inks.ink,
                           mirrored: mirrored)

        // The summary in moss, the snippet, or a receipt's amount.
        if let gist = lines.gist {
            for (index, line) in gistLines(gist, width: width, count: measure.lines(shape)).enumerated() {
                let top = stops.third + CGFloat(index) * measure.gistLine
                TextLine(line).draw(in: context, x: textX,
                                    baseline: RowFaces.baseline(RowFaces.gist, top: top, height: measure.gistLine),
                                    width: width, color: inks.moss, mirrored: mirrored)
            }
        } else {
            let baseline = RowFaces.baseline(RowFaces.preview, top: stops.third, height: RowMeasure.previewLine)
            var at = textX
            if let amount = lines.amount {
                amount.draw(in: context, x: at, baseline: baseline, width: width, color: inks.ink, mirrored: mirrored)
                at += amount.width
            }
            lines.preview?.draw(in: context, x: at, baseline: baseline, width: right - at, color: inks.ink3,
                                mirrored: mirrored)
        }

        if let top = stops.chips {
            drawChips(lines.chips, top: top, from: textX, to: right, inks: inks, context: context)
        }
        drew.acts = state.hovering && !acts.isEmpty
        if drew.acts {
            drawActs(inks, behind: fill ?? inks.paper2)
        }
    }

    /// The row's colour: chosen, checked among several, or under the pointer; else the panel's paper shows.
    private func background(_ inks: RowInks) -> NSColor? {
        if state.checked { return inks.checked }
        if state.chosen { return inks.primaryWash }
        if state.hovering { return inks.paper2 }
        return nil
    }

    private func drawAvatar(_ row: API.FolderRow, _ initials: TextLine, of name: String, look: RowLook, ring: NSColor,
                            in context: CGContext) {
        let inks = look.inks
        let disc = mirrored(avatar)
        let colours = inks.avatar(name)
        colours.fill.setFill()
        NSBezierPath(ovalIn: disc).fill()
        initials.draw(in: context, x: disc.midX - initials.width / 2,
                      baseline: RowFaces.baseline(RowFaces.initials, top: disc.minY + 11, height: 12),
                      width: disc.width, color: colours.ink, mirrored: { $0 })
        // The account's dot at the avatar's foot, ringed in the row's colour.
        let dot = mirrored(NSRect(x: avatar.maxX + 2 - 12, y: avatar.maxY + 2 - 12, width: 12, height: 12))
        ring.setFill()
        NSBezierPath(ovalIn: dot).fill()
        look.colour(of: row.account).setFill()
        NSBezierPath(ovalIn: dot.insetBy(dx: 2.5, dy: 2.5)).fill()

        // The check box over the avatar (`.ckface`), under the pointer, checked, or while rows are being checked.
        drew.check = state.hovering || state.checked || state.selecting
        guard drew.check else { return }
        let face = disc.insetBy(dx: 5, dy: 5)
        let box = NSBezierPath(roundedRect: face.insetBy(dx: 1, dy: 1), xRadius: 6, yRadius: 6)
        box.lineWidth = 2
        (state.checked ? inks.primary : inks.paperRaised).setFill()
        box.fill()
        (state.checked ? inks.primary : (state.target == .check ? inks.ink2 : inks.ink3)).setStroke()
        box.stroke()
        if state.checked {
            inks.icon(.check, size: 14, color: inks.onPrimary, stroke: 2.6)
                .draw(in: NSRect(x: face.midX - 7, y: face.midY - 7, width: 14, height: 14))
        }
    }

    /// A star, filled: a row the person starred.
    private func drawStar(in frame: NSRect, color: NSColor, context: CGContext) {
        let path = MeadowIcon.star.path(size: frame.width).cgPath
        context.saveGState()
        context.translateBy(x: frame.minX, y: frame.minY)
        context.addPath(path)
        context.setFillColor(color.cgColor)
        context.setStrokeColor(color.cgColor)
        context.setLineWidth(MeadowIcon.stroke * frame.width / 24)
        context.setLineJoin(.round)
        context.drawPath(using: .fillStroke)
        context.restoreGState()
    }

    /// The summary cut into as many lines as the density shows, the last ending in an ellipsis when there is more.
    private func gistLines(_ text: NSAttributedString, width: CGFloat, count: Int) -> [CTLine] {
        if let cut = gistCut, cut.width == width { return cut.lines }
        let lines = TextLine.clamp(text, width: width, lines: count)
        gistCut = (width, lines)
        return lines
    }

    private func drawChips(_ chips: [(chip: RowWords.Chip, text: TextLine)], top: CGFloat, from: CGFloat,
                           to right: CGFloat, inks: RowInks, context: CGContext) {
        var x = from
        for (chip, text) in chips {
            let sun = chip.tone == .sun
            let lead: CGFloat = sun ? 7 + 5 : 0
            let room = right - x
            guard room > 40 else { break }
            let width = min(8 + lead + text.width + 8, room)
            let frame = NSRect(x: x, y: top, width: width, height: RowMeasure.chip)
            (sun ? inks.sunWash : inks.paper3).setFill()
            NSBezierPath(roundedRect: mirrored(frame), xRadius: 11, yRadius: 11).fill()
            if sun {
                inks.sun.setFill()
                NSBezierPath(ovalIn: mirrored(NSRect(x: x + 8, y: frame.midY - 3.5, width: 7, height: 7))).fill()
            }
            text.draw(in: context, x: x + 8 + lead,
                      baseline: RowFaces.baseline(RowFaces.chip, top: top, height: RowMeasure.chip),
                      width: width - 16 - lead, color: sun ? inks.sunInk : inks.ink2, mirrored: mirrored)
            x += width + 6
        }
    }

    private func drawActs(_ inks: RowInks, behind: NSColor) {
        behind.setFill()
        NSBezierPath(roundedRect: mirrored(actsStrip), xRadius: 8, yRadius: 8).fill()
        for (index, act) in acts.enumerated() {
            let frame = mirrored(actFrame(index))
            let del = act == .delete
            if state.target == .act(act) {
                (del ? inks.rustWash : inks.paper3).setFill()
                NSBezierPath(roundedRect: frame, xRadius: 8, yRadius: 8).fill()
            }
            inks.icon(act.icon, size: 18, color: del ? inks.rust : inks.ink2)
                .draw(in: NSRect(x: frame.midX - 9, y: frame.midY - 9, width: 18, height: 18))
        }
    }

    private func drawWaiting(_ inks: RowInks, measure: RowMeasure) {
        inks.paper3.setFill()
        NSBezierPath(ovalIn: mirrored(avatar)).fill()
        let textX = box.minX + RowMeasure.textX
        let width = max(0, box.maxX - RowMeasure.side - textX)
        var top = box.minY + measure.padTop + 5
        for length in [140, 260, 320, 220] as [CGFloat] {
            NSBezierPath(roundedRect: mirrored(NSRect(x: textX, y: top, width: min(length, width), height: 10)),
                         xRadius: 4, yRadius: 4).fill()
            top += 21
        }
    }

    // MARK: VoiceOver

    // The row is drawn, not made of text fields, so VoiceOver reads it as one piece of text in the page's order.
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .staticText }
    override func accessibilityLabel() -> String? {
        guard let row else { return String(localized: "Loading") }
        var said: [String] = []
        if let head { said.append(head) }
        if !row.seen { said.append(String(localized: "Unread")) }
        said.append(lines?.named.name ?? row.sender)
        if let count = row.conversationCount { said.append(String(localized: "\(count) messages")) }
        said.append(row.subject)
        said.append(row.when)
        if row.flagged { said.append(String(localized: "Starred")) }
        if row.hasAttach { said.append(String(localized: "Has attachments")) }
        if let account { said.append(String(localized: "Account: \(account)")) }
        switch RowWords.third(row) {
        case .gist(let text): said.append(String(localized: "MailMind summary: \(text)"))
        case .amount(let money, let snippet): said.append("\(money), \(snippet)")
        case .preview(let snippet): said.append(snippet)
        }
        for chip in RowWords.chips(row) {
            said.append(chip.tone == .sun ? String(localized: "Next step: \(chip.text)") : chip.text)
        }
        return said.filter { !$0.isEmpty }.joined(separator: ", ")
    }
}

/// Today's brief as the Inbox's first row (`.brief-row`): the little scene, "Today's brief" and the date, the tally,
/// and who needs you in moss.
final class BriefRowCell: NSTableCellView {
    static let identifier = NSUserInterfaceItemIdentifier("brief")

    private var line: BriefLine?
    private var look: RowLook?
    private var texts: (who: TextLine, date: TextLine, tally: TextLine, gist: NSAttributedString, gistWidth: Float)?
    var state = RowState() {
        didSet { if state != oldValue { needsDisplay = true } }
    }

    init() {
        super.init(frame: NSRect(x: 0, y: 0, width: 400, height: 107))
        identifier = Self.identifier
        wantsLayer = true
        layerContentsRedrawPolicy = .onSetNeedsDisplay
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not from a nib") }

    override var isFlipped: Bool { true }

    func show(_ line: BriefLine, look: RowLook) {
        guard line != self.line || look.inks !== self.look?.inks || look.measure != self.look?.measure else { return }
        self.line = line
        self.look = look
        texts = (TextLine(String(localized: "Today’s brief"), RowFaces.whoUnread), TextLine(line.date, RowFaces.time),
                 TextLine(line.tally, RowFaces.subject), RowFaces.gistText(line.gist), RowFaces.gistWidth(line.gist))
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let look, let texts, let context = NSGraphicsContext.current?.cgContext else { return }
        let inks = look.inks
        let measure = look.measure
        let rtl = userInterfaceLayoutDirection == .rightToLeft
        func mirrored(_ rect: NSRect) -> NSRect {
            rtl ? NSRect(x: bounds.width - rect.maxX, y: rect.minY, width: rect.width, height: rect.height) : rect
        }
        context.textMatrix = CGAffineTransform(scaleX: 1, y: -1)
        let box = NSRect(x: RowMeasure.listSide, y: 0, width: bounds.width - 2 * RowMeasure.listSide,
                         height: bounds.height)
        let fill: NSColor? = state.chosen ? inks.primaryWash : state.hovering ? inks.paper2 : nil
        if let fill {
            fill.setFill()
            NSBezierPath(roundedRect: mirrored(box), xRadius: 12, yRadius: 12).fill()
        }
        if state.chosen, state.keyed {
            inks.focus.setStroke()
            let ring = NSBezierPath(roundedRect: mirrored(box).insetBy(dx: 1, dy: 1), xRadius: 11, yRadius: 11)
            ring.lineWidth = 2
            ring.stroke()
        }
        let shape = RowShape.brief.fitting(texts.gistWidth, in: bounds.width)
        let stops = measure.stops(shape)
        let thumb = mirrored(NSRect(x: box.minX + RowMeasure.side, y: stops.who, width: RowMeasure.avatar,
                                    height: RowMeasure.avatar))
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath(roundedRect: thumb, xRadius: 10, yRadius: 10).addClip()
        inks.briefScene.draw(in: thumb)
        NSGraphicsContext.restoreGraphicsState()

        let textX = box.minX + RowMeasure.textX
        let right = box.maxX - RowMeasure.side
        let whoBaseline = RowFaces.baseline(RowFaces.who, top: stops.who, height: RowMeasure.line)
        texts.date.draw(in: context, x: right - texts.date.width, baseline: whoBaseline, width: texts.date.width,
                        color: inks.ink3, mirrored: mirrored)
        texts.who.draw(in: context, x: textX, baseline: whoBaseline, width: right - texts.date.width - 8 - textX,
                       color: inks.ink, mirrored: mirrored)
        texts.tally.draw(in: context, x: textX,
                         baseline: RowFaces.baseline(RowFaces.subject, top: stops.subject, height: RowMeasure.line),
                         width: right - textX, color: inks.ink, mirrored: mirrored)
        let gist = TextLine.clamp(texts.gist, width: right - textX, lines: measure.lines(shape))
        for (index, line) in gist.enumerated() {
            let top = stops.third + CGFloat(index) * measure.gistLine
            TextLine(line).draw(in: context, x: textX,
                                baseline: RowFaces.baseline(RowFaces.gist, top: top, height: measure.gistLine),
                                width: right - textX, color: inks.moss, mirrored: mirrored)
        }
    }

    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .button }
    override func accessibilityLabel() -> String? {
        guard let line else { return String(localized: "Today’s brief") }
        return [String(localized: "Today’s brief"), line.date, line.tally, line.gist].joined(separator: ", ")
    }
}
#endif
