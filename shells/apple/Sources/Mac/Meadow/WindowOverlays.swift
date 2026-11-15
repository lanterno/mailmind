#if os(macOS)
import SwiftUI

/// What stands on the window's scrim, one at a time (the mockup's `openOverlay()`): the palette, with the words it
/// opens on (`? ` to ask, `! ` and a command for a bulk action), Move To, Save View, or the keyboard's legend.
enum WindowDialog: Equatable {
    case palette(initial: String)
    case move(MoveAsk)
    case saveView(String)
    case keys

    /// Which dialog it is, whatever it holds: a dialog drawn afresh only when this changes.
    var kind: String {
        switch self {
        case .palette: "palette"
        case .move: "move"
        case .saveView: "save-view"
        case .keys: "keys"
        }
    }

    /// The one to show of all asked for: what a person was asked to answer (a move's folder, a view's name), then
    /// what the desk opened (Ask, a bulk action), the palette, and the legend.
    static func shown(moving: MoveAsk?, savingView: String?, sheet: Desk.Sheet?, planned: String?, palette: Bool,
                      keys: Bool) -> WindowDialog? {
        if let moving { return .move(moving) }
        if let savingView { return .saveView(savingView) }
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
/// the mockup's scrim blurs the page; the snooze chooser beside the pane's Snooze, or near the middle when it isn't
/// there; and the toasts at the window's foot. The window applies it inside `DeskSurfaces`, so the desk is here.
struct WindowOverlays: ViewModifier {
    let mailbox: Mailbox
    var status = AppStatus.shared
    @Environment(Desk.self) private var desk: Desk?
    @State private var anchors = MeadowAnchors()
    @State private var snoozeMenu = MeadowMenuModel()

    func body(content: Content) -> some View {
        let dialog = WindowDialog.shown(moving: mailbox.moving, savingView: mailbox.savingView, sheet: desk?.sheet,
                                        planned: desk?.plan.command, palette: mailbox.paletteOpen,
                                        keys: status.showingKeys)
        content
            .blur(radius: dialog == nil ? 0 : 2)
            .environment(\.meadowAnchors, anchors)
            .overlay(alignment: .topLeading) { snoozeAnchor }
            .overlay(alignment: .bottom) {
                WindowToasts(mailbox: mailbox, status: status, desk: desk)
                    .padding(.bottom, 20)
            }
            .overlay {
                if let dialog {
                    view(of: dialog)
                        .id(dialog.kind)
                        .transition(.opacity)
                }
            }
            .animation(.easeOut(duration: 0.12), value: dialog?.kind)
            // What a dialog types is its own: the list's keys and the frame's Esc wait while one is up.
            .onChange(of: dialog != nil) { _, up in mailbox.typing = up }
            .onChange(of: mailbox.typing) { _, now in if !now && dialog != nil { mailbox.typing = true } }
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
        case .saveView(let query):
            MeadowDialog(place: .form, close: close) {
                SaveViewSheet(mailbox: mailbox, query: query, close: close)
            }
        case .keys:
            MeadowDialog(place: .keys, close: close) {
                KeysLegend(status: status, close: close)
            }
        }
    }

    /// Asking and acting, where the window has an assistant and AI is on.
    private var modes: [Entries.Mode] {
        guard let desk, mailbox.aiOn else { return [] }
        return desk.paletteModes(undoKey: mailbox.singleKeys ? "Z" : "⌘Z")
    }

    /// Close whatever dialog is up, and stop a plan it was showing that hasn't run.
    private func close() {
        mailbox.paletteOpen = false
        mailbox.moving = nil
        mailbox.savingView = nil
        status.showingKeys = false
        guard let desk else { return }
        switch desk.sheet {
        case .ask?, .plan?: desk.sheet = nil
        default: break
        }
        if desk.plan.doing != nil { desk.plan.stop() }
    }

    /// The snooze chooser hangs from where the pane's Snooze stands, or near the middle of the window.
    @ViewBuilder private var snoozeAnchor: some View {
        if case .snoozeUntil(let copies, let canUnsnooze)? = desk?.sheet, let desk {
            GeometryReader { window in
                let at = anchors.frames["snooze"].map { $0.offsetBy(dx: -window.frame(in: .global).minX,
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
            if let toast = mailbox.toast {
                MailboxToast(mailbox: mailbox, toast: toast)
                    .id(toast.id)
                    .transition(rise)
            }
            if let notice = status.notice {
                MeadowToast(notice.text)
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
}

/// What the mailbox last said (`toast()`), as a toast: the verb's icon, its words and the server's reason, and Undo
/// with its key while it can be taken back. VoiceOver says it as it comes.
struct MailboxToast: View {
    let mailbox: Mailbox
    let toast: Toast

    var body: some View {
        MeadowToast(icon: Self.icon(toast.text), text: Text(toast.text), detail: toast.detail) {
            if toast.undo != nil {
                MeadowToastButton(title: String(localized: "Undo"), icon: .undo,
                                  key: mailbox.singleKeys ? "Z" : "⌘Z") { mailbox.undo() }
            }
        }
        .onAppear { AccessibilityNotification.Announcement(toast.text).post() }
    }

    /// The icon for what was said, by the word the mailbox starts it with (`Words.filed`, the marks, Undo): a
    /// refusal's alert, a verb's own icon, else a tick. Words it doesn't know, a translation's among them, take the
    /// tick.
    static func icon(_ text: String) -> MeadowIcon {
        let starts: [(String, MeadowIcon)] = [
            ("Couldn’t", .alert), ("Archived", .archive), ("Trashed", .trash), ("Marked junk", .ban),
            ("Moved", .folder), ("Starred", .star), ("Unstarred", .star), ("Marked read", .mailOpen),
            ("Marked unread", .mail), ("Saved view", .bookmark), ("Snoozed", .clock), ("Muted", .bellOff),
            ("Saved to Reply later", .bookmark), ("Go to folder", .arrowRight), ("Saved ", .download),
        ]
        if let icon = starts.first(where: { text.hasPrefix($0.0) })?.1 { return icon }
        if text.hasSuffix(" undone") { return .undo }
        if text.contains(" failed") || text.contains("couldn’t") { return .alert }
        return .check
    }
}
#endif
