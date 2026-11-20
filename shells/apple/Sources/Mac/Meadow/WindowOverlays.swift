#if os(macOS)
import AppKit
import SwiftUI

/// What stands on the window's scrim, one at a time (the mockup's `openOverlay()`): the palette, with the words it
/// opens on (`? ` to ask, `! ` and a command for a bulk action), Move To, a folder's dialog, Save View, a snooze's time
/// of one's own, a sender's card, or the keyboard's legend.
enum WindowDialog: Equatable {
    case palette(initial: String)
    case move(MoveAsk)
    case folder(FolderAsk)
    case saveView(String)
    case snoozeTime([MailCopy])
    case contact(String)
    case keys

    /// Which dialog it is, whatever it holds: a dialog drawn afresh only when this changes.
    var kind: String {
        switch self {
        case .palette: "palette"
        case .move: "move"
        case .folder: "folder"
        case .saveView: "save-view"
        case .snoozeTime: "snooze-time"
        case .contact: "contact"
        case .keys: "keys"
        }
    }

    /// The one to show of all asked for: what a person was asked to answer (a folder's name or its deleting, a move's
    /// folder, a view's name, a snooze's time), a sender's card, then what the desk opened (Ask, a bulk action), the
    /// palette, and the legend.
    static func shown(moving: MoveAsk?, folder: FolderAsk? = nil, savingView: String?, choosingTime: [MailCopy]? = nil,
                      contact: String? = nil, sheet: Desk.Sheet?, planned: String?, palette: Bool,
                      keys: Bool) -> WindowDialog? {
        if let folder { return .folder(folder) }
        if let moving { return .move(moving) }
        if let savingView { return .saveView(savingView) }
        if let choosingTime { return .snoozeTime(choosingTime) }
        if let contact { return .contact(contact) }
        switch sheet {
        case .ask?: return .palette(initial: "? ")
        case .plan?: return .palette(initial: "! " + (planned ?? ""))
        default: break
        }
        if palette { return .palette(initial: "") }
        return keys ? .keys : nil
    }
}

/// What floats over the mail window (`Overlays`' blocks): a dialog on the scrim, the window blurred under it as
/// the mockup's scrim blurs the page; the snooze chooser beside the pane's Snooze, or the clock of the row it
/// snoozes, or near the middle when neither is there; and the toasts at the window's foot, over the reading pane and
/// clear of its reply box (`ToastPlace`). The window applies it inside `DeskSurfaces`, so the desk is here.
struct WindowOverlays: ViewModifier {
    let mailbox: Mailbox
    var status = AppStatus.shared
    @Environment(Desk.self) private var desk: Desk?
    @State private var anchors = MeadowAnchors()
    @State private var toastRoom = ToastRoom()
    @State private var snoozeMenu = MeadowMenuModel()
    @State private var focus = DialogFocus()
    /// The palette came up over an answer, which it leaves there as it goes.
    @State private var paletteOverAnswer = false

    private var shownDialog: WindowDialog? {
        WindowDialog.shown(moving: mailbox.moving, folder: mailbox.folderAsk, savingView: mailbox.savingView,
                           choosingTime: desk?.attention.choosingTime, contact: mailbox.contact, sheet: desk?.sheet,
                           planned: desk?.plan.command, palette: mailbox.paletteOpen, keys: status.showingKeys)
    }

    /// Whether the palette going takes the window back from an answer to where its question was asked, as the page's
    /// Esc stops asking: the palette asked it, no answer was there as it came up, and no message the answer cites was
    /// opened from it, which the answer stays beside.
    static func leavesAnswer(palette: Bool, overAnswer: Bool, place: Place?, open: MailCopy?) -> Bool {
        palette && !overAnswer && place == .desk(.ask) && open == nil
    }

    func body(content: Content) -> some View {
        let dialog = shownDialog
        content
            .blur(radius: dialog == nil ? 0 : 2)
            .environment(\.meadowAnchors, anchors)
            .environment(\.toastRoom, toastRoom)
            .background { DialogFocusWindow(focus: focus) }
            .background { TypingWatch { mailbox.editingText = $0 } }
            // The list's single keys that nothing else in the window took, as after a click on the brief's card.
            .background { UntakenKeys(mailbox: mailbox) }
            .onAppear { focus.waits = { [status] in status.firstRun } }
            .overlay(alignment: .topLeading) { snoozeAnchor }
            .overlay {
                PlacedToasts(room: toastRoom) { WindowToasts(mailbox: mailbox, status: status, desk: desk) }
            }
            .overlay {
                if let dialog {
                    view(of: dialog)
                        .id(dialog.kind)
                        .transition(.opacity)
                        .onDisappear { focus.gone() }
                        // Esc closes the dialog wherever the keyboard is in the window, not only from its field.
                        .background {
                            Button("Close", action: close)
                                .keyboardShortcut(.cancelAction)
                                .opacity(0)
                                .frame(width: 0, height: 0)
                                .accessibilityHidden(true)
                        }
                }
            }
            .animation(.easeOut(duration: 0.12), value: dialog?.kind)
            // What a dialog types is its own: the list's keys and the frame's Esc wait while one is up, and the
            // keyboard goes back to where it was once the last goes.
            .onChange(of: dialog != nil) { _, up in
                mailbox.dialogUp = up
                if up { focus.opened() } else { focus.closing() }
                // The snooze chooser floats above the scrim: a dialog (⇧⌘H's time) takes its place.
                if up, case .snoozeUntil? = desk?.sheet { desk?.sheet = nil }
            }
            // A row's clock is where its chooser hung only while that chooser is up: h and the menus hang theirs from
            // the pane's Snooze.
            .onChange(of: snoozing) { _, now in if !now { anchors.forget(MailboxTableController.rowSnooze) } }
            // A plan the palette showed and didn't run stops as the palette goes, however it goes (Esc, Move To or a
            // menu's sheet in its place), so a first press never waits armed for the palette to come back.
            .onChange(of: dialog?.kind == "palette") { _, up in
                if !up, let desk, desk.plan.doing != nil { desk.plan.stop() }
                if up { paletteOverAnswer = mailbox.app?.place == .desk(.ask) }
            }
    }

    @ViewBuilder private func view(of dialog: WindowDialog) -> some View {
        switch dialog {
        case .palette(let initial):
            MeadowDialog(close: close) {
                PaletteSheet(mailbox: mailbox, initial: initial, modes: modes, close: close)
            }
        case .move(let ask):
            MeadowDialog(close: close) {
                MoveToSheet(mailbox: mailbox, ask: ask, close: close)
            }
        case .folder(let ask):
            MeadowDialog(place: .form, close: close) {
                FolderSheet(mailbox: mailbox, ask: ask, close: close)
            }
        case .saveView(let query):
            MeadowDialog(place: .form, close: close) {
                SaveViewSheet(mailbox: mailbox, query: query, close: close)
            }
        case .snoozeTime(let copies):
            if let desk {
                MeadowDialog(place: .form, close: close) {
                    SnoozeTimeDialog(copies: copies, model: desk.attention, close: close)
                }
            }
        case .contact(let address):
            MeadowDialog(place: .card, close: close) {
                ContactCard(mailbox: mailbox, address: address, close: close)
            }
        case .keys:
            MeadowDialog(place: .keys, close: close) {
                KeysLegend(status: status, close: close)
            }
        }
    }

    /// Whether the snooze chooser is up.
    private var snoozing: Bool {
        if case .snoozeUntil? = desk?.sheet { return true }
        return false
    }

    /// Asking and acting, where the window has an assistant and AI is on.
    private var modes: [Entries.Mode] {
        guard let desk, mailbox.aiOn else { return [] }
        return desk.paletteModes(undoKey: mailbox.singleKeys ? "Z" : "⌘Z")
    }

    /// Close whatever dialog is up; the palette that asked a question takes the window back from its answer.
    private func close() {
        var palette = false
        if case .palette? = shownDialog { palette = true }
        if Self.leavesAnswer(palette: palette, overAnswer: paletteOverAnswer, place: mailbox.app?.place,
                             open: mailbox.app?.selection.copy) {
            mailbox.leaveAsk()
        }
        mailbox.paletteOpen = false
        mailbox.moving = nil
        mailbox.folderAsk = nil
        mailbox.savingView = nil
        mailbox.contact = nil
        status.showingKeys = false
        guard let desk else { return }
        desk.attention.choosingTime = nil
        switch desk.sheet {
        case .ask?, .plan?: desk.sheet = nil
        default: break
        }
    }

    /// The snooze chooser hangs from the row's clock it was opened from, else from where the pane's Snooze stands, or
    /// near the middle of the window.
    @ViewBuilder private var snoozeAnchor: some View {
        if case .snoozeUntil(let copies, let canUnsnooze)? = desk?.sheet, let desk {
            GeometryReader { window in
                let button = anchors.frames[MailboxTableController.rowSnooze]
                    ?? anchors.snooze(for: copies, open: mailbox.app?.selection.copy)
                let at = button.map { $0.offsetBy(dx: -window.frame(in: .global).minX,
                                                  dy: -window.frame(in: .global).minY) }
                    ?? CGRect(x: window.size.width / 2 - 130, y: window.size.height / 3, width: 0, height: 0)
                Color.clear
                    .frame(width: at.width, height: at.height)
                    .meadowPopover(isPresented: Binding(get: { true }, set: { if !$0 { desk.sheet = nil } }),
                                   keys: { snoozeMenu.key($0) }) {
                        SnoozeChooser(copies: copies, model: desk.attention, canUnsnooze: canUnsnooze,
                                      menu: snoozeMenu) { desk.sheet = nil }
                    }
                    .padding(.leading, at.minX)
                    .padding(.top, at.minY)
                    .onAppear { snoozeMenu.at = 0 }
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)
        }
    }
}

/// The keyboard around the window's dialogs: as one comes up, whatever had the keyboard lets it go, so the list's
/// arrows and keys wait behind the scrim and the search field's typing stops, and the dialog takes it; once the last
/// has gone, the keyboard goes back to what had it before (the list's table, as a rule), unless something the dialog
/// opened has taken it since. Between dialogs, a keyboard that is nowhere in the window (a field let it go, the list
/// was swapped for another place's) goes to the list.
@MainActor
final class DialogFocus {
    weak var window: NSWindow?
    /// What had the keyboard before the dialog came up.
    private weak var before: NSResponder?
    /// What the dialog had as it went: while that or nothing has the keyboard, it goes back.
    private weak var dialogHad: NSResponder?
    private var going = false
    /// A dialog is up, whose keyboard it is.
    private(set) var up = false
    /// Whether something over the window holds back the list's keys besides a dialog: the first run's welcome.
    var waits: @MainActor () -> Bool = { false }

    func opened() {
        going = false
        up = true
        guard let window else { return }
        before = Self.owner(of: window.firstResponder)
        if before != nil { window.makeFirstResponder(nil) }
    }

    func closing() {
        going = true
        up = false
        dialogHad = window.flatMap { Self.owner(of: $0.firstResponder) }
        // Its going may be heard before this, as its fade ends: the keyboard comes back all the same.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in self?.gone() }
    }

    /// The dialog has left the window.
    func gone() {
        guard going, let window else { return }
        going = false
        if let back = Self.back(to: before, in: window, dialogHad: dialogHad) { window.makeFirstResponder(back) }
        (before, dialogHad) = (nil, nil)
    }

    /// Nothing in the window has the keyboard: the list takes it, unless a dialog is up or going, which hands it on
    /// itself.
    func settle() {
        guard !up, !going, !waits(), let window, Self.nowhere(window.firstResponder, in: window),
              let table = ListTable.find(in: window.contentView) else { return }
        window.makeFirstResponder(table)
    }

    /// Whether `responder` leaves the keyboard nowhere in `window`: none, the window itself, or a view no longer in it.
    static func nowhere(_ responder: NSResponder?, in window: NSWindow) -> Bool {
        guard let responder, responder !== window else { return true }
        if let view = responder as? NSView { return view.window !== window }
        return false
    }

    /// What stands for a responder: a field for its editor, nothing for the window itself.
    static func owner(of responder: NSResponder?) -> NSResponder? {
        if let editor = responder as? NSTextView, editor.isFieldEditor, let field = editor.delegate as? NSView {
            return field
        }
        return responder is NSWindow ? nil : responder
    }

    /// Where the keyboard goes as a dialog goes: to what had it before, while that is still in the window, else to
    /// the list's table; nowhere while something the dialog didn't have holds it.
    static func back(to before: NSResponder?, in window: NSWindow, dialogHad: NSResponder?) -> NSResponder? {
        if let now = owner(of: window.firstResponder), now !== dialogHad, (now as? NSView)?.window === window {
            return nil
        }
        if let before = before as? NSView, before.window === window { return before }
        return ListTable.find(in: window.contentView)
    }
}

/// Tells the window's `DialogFocus` which window it is, and when the keyboard moves in it.
struct DialogFocusWindow: NSViewRepresentable {
    let focus: DialogFocus

    func makeNSView(context: Context) -> NSView { Probe(focus) }

    func updateNSView(_ view: NSView, context: Context) {}

    final class Probe: NSView {
        let focus: DialogFocus
        private var watching: NSKeyValueObservation?

        init(_ focus: DialogFocus) {
            self.focus = focus
            super.init(frame: .zero)
        }

        required init?(coder: NSCoder) { nil }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            watching = nil
            // SwiftUI may make this view again and take the old one out after the new one is in: the one leaving
            // says nothing, or it would leave the focus with no window.
            guard let window else { return }
            focus.window = window
            // Once whatever let the keyboard go has finished moving it (SwiftUI hands a field's focus on a moment
            // later), a keyboard left nowhere goes to the list.
            watching = window.observe(\.firstResponder, options: [.new]) { [weak focus] _, _ in
                DispatchQueue.main.async { MainActor.assumeIsolated { focus?.settle() } }
            }
        }
    }
}

/// Where the toasts may stand clear of: the reading pane, and the reply box at its foot, where each last stood in the
/// window. Only the toasts read it, so a frame that moves as the message scrolls draws them again and nothing else.
@MainActor
@Observable
final class ToastRoom {
    enum Part { case pane, reply }

    private(set) var pane: CGRect?
    private(set) var reply: CGRect?

    func set(_ part: Part, _ frame: CGRect?) {
        switch part {
        case .pane: if pane != frame { pane = frame }
        case .reply: if reply != frame { reply = frame }
        }
    }
}

extension EnvironmentValues {
    /// The window's room for its toasts; none where a view is drawn away from a window's.
    @Entry var toastRoom: ToastRoom?
}

extension View {
    /// Tell the window's toasts where this stands (`ToastRoom`), so they stand clear of it.
    func toastRoom(_ part: ToastRoom.Part) -> some View {
        modifier(ToastRoomPart(part: part))
    }
}

private struct ToastRoomPart: ViewModifier {
    let part: ToastRoom.Part
    @Environment(\.toastRoom) private var room

    func body(content: Content) -> some View {
        content
            .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { room?.set(part, $0) }
            .onDisappear { room?.set(part, nil) }
    }
}

/// Where the toasts stand: at the window's foot, centred over the reading pane where it shows, else over the window,
/// and lifted over the reply box where it stands at the pane's foot, so a toast covers no quick reply or button.
enum ToastPlace {
    /// How high over the window's foot they stand, with nothing in the way.
    static let foot: CGFloat = 20
    /// The most a toast stands high: a word, a reason under it, and a button.
    static let tallest: CGFloat = 64
    /// The room between a toast and the reply box it stands over.
    static let gap: CGFloat = 12

    /// The least room between a toast and the window's sides.
    static let margin: CGFloat = 16

    /// How far right of the window's middle toasts `wide` points wide stand, asked to stand `dx` right of it: as
    /// asked, or as near it as keeps them `margin` inside the window's sides, so a long toast centred over the pane
    /// never runs past the window's edge with its Undo.
    static func inside(_ dx: CGFloat, wide: CGFloat, window: CGFloat) -> CGFloat {
        let most = max(0, (window - wide) / 2 - margin)
        return min(max(dx, -most), most)
    }

    /// How far right of the window's middle the toasts stand (`dx`), and how high over its foot (`bottom`).
    static func of(window: CGRect, pane: CGRect?, reply: CGRect?) -> (dx: CGFloat, bottom: CGFloat) {
        let pane = pane.flatMap { $0.intersects(window) ? $0 : nil }
        let dx = pane.map { $0.midX - window.midX } ?? 0
        let band = (top: window.maxY - foot - tallest, bottom: window.maxY - foot)
        guard let reply, pane.map({ $0.intersects(reply) }) ?? true,
              reply.minY < band.bottom, reply.maxY > band.top else { return (dx, foot) }
        return (dx, max(foot, window.maxY - reply.minY + gap))
    }
}

/// The toasts in their place (`ToastPlace`): the mail window's, and a message's own window's.
struct PlacedToasts<Toasts: View>: View {
    let room: ToastRoom
    @ViewBuilder let toasts: () -> Toasts
    /// How wide the toasts stand, which keeps them inside the window.
    @State private var wide: CGFloat = 0

    var body: some View {
        GeometryReader { proxy in
            let place = ToastPlace.of(window: proxy.frame(in: .global), pane: room.pane, reply: room.reply)
            toasts()
                // No wider than the window leaves room for: a long toast's words wrap.
                .frame(maxWidth: max(0, proxy.size.width - 2 * ToastPlace.margin))
                .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { wide = $0 }
                .padding(.bottom, place.bottom)
                .offset(x: ToastPlace.inside(place.dx, wide: wide, window: proxy.size.width))
                .frame(width: proxy.size.width, height: proxy.size.height, alignment: .bottom)
                .animation(.snappy(duration: 0.2), value: place.bottom)
        }
    }
}

/// The toasts at the window's foot (`.toasts`): a send's float and what writing says, what the mailbox, the status and
/// the desk say for a moment, each with its Undo where it has one.
struct WindowToasts: View {
    let mailbox: Mailbox
    let status: AppStatus
    let desk: Desk?

    var body: some View {
        let rise = AnyTransition.move(edge: .bottom).combined(with: .opacity)
        VStack(spacing: 8) {
            SendFloatView()
                .transition(rise)
            if let toast = mailbox.toast, Self.saysMailboxToast(under: desk?.sheet) {
                MailboxToast(mailbox: mailbox, toast: toast)
                    .id(toast.id)
                    .transition(rise)
            }
            if let notice = status.notice {
                MeadowToast(icon: MailboxToast.icon(notice.text, refused: notice.refused), notice.text)
                    .id(notice.id)
                    .transition(rise)
            }
            if let desk {
                DeskNoticeBanner(notices: desk.notices) { Task { await desk.undo() } }
                    .transition(rise)
            }
        }
        .animation(.snappy(duration: 0.2), value: mailbox.toast)
        .animation(.snappy(duration: 0.2), value: status.notice)
        .animation(.snappy(duration: 0.2), value: desk?.notices.shown)
        .animation(.snappy(duration: 0.2), value: Composing.shared.float)
        .animation(.snappy(duration: 0.2), value: Composing.shared.notice)
    }

    /// Whether the window's foot says the mailbox's toast: not while a sheet that says it on itself is up (Curate's,
    /// `DeskSheetToast`), so it is said once, there.
    static func saysMailboxToast(under sheet: Desk.Sheet?) -> Bool {
        sheet?.saysToasts != true
    }
}

/// What the mailbox last said (`toast()`), as a toast: the verb's icon, its words and the server's reason, and Undo
/// with its key while it can be taken back. VoiceOver says it as it comes.
struct MailboxToast: View {
    let mailbox: Mailbox
    let toast: Toast

    var body: some View {
        MeadowToast(icon: Self.icon(toast.text, refused: toast.refused), text: Text(toast.text), detail: toast.detail) {
            if toast.undo != nil {
                MeadowToastButton(title: String(localized: "Undo"), icon: .undo,
                                  key: mailbox.singleKeys ? "Z" : "⌘Z") { mailbox.undo() }
            }
        }
        .onAppear { AccessibilityNotification.Announcement(toast.text).post() }
    }

    /// The icon for what was said: a refusal's alert, never the tick, for words said as one (`refused`) or that say
    /// one (read only's sentence, "Couldn’t…", "Too late…"); an Undo's, whatever verb it names ("Trashed undone");
    /// else by the word the mailbox starts it with (`Words.filed`, the marks), a verb's own icon, else a tick. Words
    /// it doesn't know, a translation's among them, take the tick.
    static func icon(_ text: String, refused: Bool = false) -> MeadowIcon {
        if refused || text == ReadOnlyWords.refused { return .alert }
        if text.hasSuffix(" undone") { return .undo }
        let starts: [(String, MeadowIcon)] = [
            ("Couldn’t", .alert), ("Too late", .alert), ("Nothing to", .alert), ("Archived", .archive), ("Trashed", .trash), ("Marked junk", .ban),
            ("Moved", .folder), ("Starred", .star), ("Unstarred", .star), ("Marked read", .mailOpen),
            ("Marked unread", .mail), ("Saved view", .bookmark), ("Snoozed", .clock), ("Muted", .bellOff),
            ("Saved to Reply later", .bookmark), ("Go to folder", .arrowRight), ("Saved ", .download),
        ]
        if let icon = starts.first(where: { text.hasPrefix($0.0) })?.1 { return icon }
        if text.contains(" failed") || text.contains("couldn’t") { return .alert }
        return .check
    }
}
#endif
