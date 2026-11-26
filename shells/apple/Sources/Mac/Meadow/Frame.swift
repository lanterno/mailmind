#if os(macOS)
import SwiftUI

// The window as Meadow draws it (#44), the mockup's `.top` over its `.shell`: the sky, the top bar, and below it the
// rail, the list and the pane as panels of paper, or a place of its own across the list's and the pane's room, or
// only the pane while reading in focus. Under 1100 wide the rail is a drawer over the shell.
//
// Each panel's view lives as long as the window, and a panel the shell hides is set aside off the window rather than
// taken down: the list's table, the reader and the hooks they hang on the mailbox carry on as they were.

extension EnvironmentValues {
    /// The reading pane's width, which its bar folds to (the mockup's `@container pane`).
    @Entry var meadowPaneWidth: CGFloat = 1000
    /// Whether the rail shows its places' names as well as their icons: open in a wide window, or the drawer.
    @Entry var meadowRailOpen = false
}

extension FocusedValues {
    /// The key window's shell, for the View menu's commands.
    @Entry var meadowShell: MeadowShell?
}

struct MeadowFrame<Pane: View>: View {
    let app: MailApp
    @Bindable var mailbox: Mailbox
    @State private var shell: MeadowShell
    /// What the top bar's status and the alert under it say.
    private let status: AppStatus
    /// Whether the traffic lights stand at the top bar's left, as in a window; a test's picture has none.
    private let lights: Bool
    private let pane: Pane
    @Environment(\.meadow) private var meadow
    @Environment(\.accessibilityReduceMotion) private var still

    init(app: MailApp, mailbox: Mailbox, shell: MeadowShell? = nil, status: AppStatus = .shared, lights: Bool = true,
         @ViewBuilder pane: () -> Pane) {
        self.app = app
        self.mailbox = mailbox
        _shell = State(initialValue: shell ?? MeadowShell())
        self.status = status
        self.lights = lights
        self.pane = pane()
    }

    /// A place of its own across the list's and the pane's room, while no message is open over it.
    private var spans: Bool {
        (app.place?.spansTheWindow ?? false) && app.selection.copy == nil
    }

    var body: some View {
        VStack(spacing: 0) {
            MeadowTopBar(app: app, mailbox: mailbox, shell: shell, status: status,
                         lights: lights ? WindowLights.room : 0)
                // The search's suggestions hang over the panels.
                .zIndex(1)
            // The list says itself when its accounts can't connect (`OfflineNote`); the alert is for a place with no
            // list on screen.
            if spans { MeadowAccountAlert(status: status) }
            GeometryReader { area in
                let layout = ShellLayout(size: area.size, railOpen: shell.railOpen, listWidth: shell.listWidth,
                                         mode: shell.mode(spanning: spans))
                panels(layout)
                    .onChange(of: area.size.width, initial: true) { _, width in shell.width = width }
            }
        }
        .background { MeadowSky().ignoresSafeArea() }
        .ignoresSafeArea()
        .meadowWindowChrome(on: lights)
        .background { escape }
        .environment(shell)
        .focusedSceneValue(\.meadowShell, shell)
        .navigationTitle(mailbox.title)
        .onAppear(perform: joinMailbox)
        // Another window let go of the mailbox's hooks as it closed: this one's hang theirs again.
        .onChange(of: mailbox.joinRound) { joinMailbox() }
        .onChange(of: app.place) { _, place in
            shell.drawerOpen = false
            if place?.spansTheWindow == true { shell.focus = false }
        }
        .onChange(of: app.selection.copy == nil) { _, none in
            // Focus reading is reading: with nothing open there is nothing to focus on.
            if none { shell.focus = false }
        }
    }

    // MARK: The panels

    private func panels(_ layout: ShellLayout) -> some View {
        let animation: Animation? = still ? nil : .timingCurve(0.22, 0.8, 0.3, 1, duration: 0.32)
        let railOpen = !layout.docked || shell.railOpen
        return ZStack(alignment: .topLeading) {
            // The list's column stands in the list's room, or across the list's and the pane's for a place that spans.
            MailboxColumn(app: app, mailbox: mailbox)
                .meadowPanel()
                .placed(layout.list ?? layout.full, named: String(localized: "Message list"))
                // The end-to-end framework's anchor (tests/framework/anchors.toml): the list, whatever it shows.
                .accessibilityIdentifier("list")
            pane
                .environment(\.meadowPaneWidth, layout.pane?.width ?? 0)
                .linkStatusLine()
                .meadowPanel()
                .toastRoom(.pane)
                .placed(layout.pane, named: String(localized: "Reading pane"))
                .accessibilityIdentifier("reader")
            if let splitter = layout.splitter {
                MeadowSplitter(shell: shell)
                    .frame(width: splitter.width, height: splitter.height)
                    .position(x: splitter.midX, y: splitter.midY)
            }
            if !layout.docked, shell.drawerOpen {
                meadow.scrim
                    .contentShape(Rectangle())
                    .onTapGesture { shell.drawerOpen = false }
                    .accessibilityLabel("Close mailboxes")
                    .accessibilityAddTraits(.isButton)
                    .transition(.opacity)
            }
            rail(layout)
                .environment(\.meadowRailOpen, railOpen)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .animation(animation, value: shell.drawerOpen)
    }

    /// The rail: a panel at the shell's left, or the drawer over it in a narrow window, slid out of sight while shut.
    @ViewBuilder private func rail(_ layout: ShellLayout) -> some View {
        let sidebar = MailboxSidebar(app: app, mailbox: mailbox)
            .scrollContentBackground(.hidden)
        let name = String(localized: "Mailboxes")
        if let rect = layout.rail {
            sidebar
                .meadowPanel(opacity: 0.86)
                .placed(rect, named: name)
        } else if layout.docked {
            // Focus reading: the rail stands aside.
            sidebar.placed(nil, named: name)
        } else {
            let open = shell.drawerOpen
            sidebar
                .modifier(MeadowDrawer())
                .frame(width: layout.drawer.width, height: layout.drawer.height)
                .offset(x: open ? 0 : -(layout.drawer.width * 1.02 + 40))
                .voiceOverGroup(name, hidden: !open)
                .allowsHitTesting(open)
        }
    }

    // MARK: Keys

    /// Esc closes the drawer or leaves focus reading, ahead of the list's own Esc, while either is there to undo and
    /// nothing is being typed; on an answer, where no list is, it goes back to where the question was asked.
    @ViewBuilder private var escape: some View {
        if (shell.drawerOpen || shell.focus) && !mailbox.typing {
            Button("Leave Focus Reading") { shell.escape() }
                .keyboardShortcut(.cancelAction)
                .opacity(0)
                .frame(width: 0, height: 0)
                .accessibilityHidden(true)
        } else if app.place == .desk(.ask) && !mailbox.typing {
            Button("Back") { mailbox.leaveAsk() }
                .keyboardShortcut(.cancelAction)
                .opacity(0)
                .frame(width: 0, height: 0)
                .accessibilityHidden(true)
        }
    }

    /// The list's single keys for the shell: `[` folds or opens the rail, Enter reads the open message in focus.
    private func joinMailbox() {
        let shell = shell, app = app
        mailbox.join("meadow") { mailbox in
            mailbox.keys["["] = { shell.toggleRail() }
            let focus: @MainActor () -> Void = { if app.selection.copy != nil { shell.toggleFocus() } }
            mailbox.keys["\r"] = focus
            mailbox.keys["\u{3}"] = focus
        }
    }
}

extension View {
    /// Stand in `rect` of the shell, or, with none, aside off the window: hidden, out of reach and out of
    /// VoiceOver's way, but still alive. VoiceOver finds it as a group, named `label` where one is given.
    func placed(_ rect: CGRect?, named label: String? = nil) -> some View {
        let shown = rect ?? CGRect(x: -40_000, y: 0, width: 480, height: 480)
        return frame(width: shown.width, height: shown.height)
            .position(x: shown.midX, y: shown.midY)
            .opacity(rect == nil ? 0 : 1)
            .allowsHitTesting(rect != nil)
            .voiceOverGroup(label, hidden: rect == nil)
    }

    /// One group for VoiceOver, named `label` where one is given, hidden whole while `hidden`. Hidden as a group, so
    /// shown it never says `accessibilityHidden(false)` to what it holds: that would show VoiceOver the scenery hidden
    /// inside it, a panel's paper and grain as a nameless picture and its shadows as nameless groups.
    func voiceOverGroup(_ label: String?, hidden: Bool) -> some View {
        accessibilityElement(children: .contain)
            .accessibilityLabel(Text(label ?? ""))
            .accessibilityHidden(hidden)
    }
}

/// The rail as a drawer (`.rail` under 1100 wide): paper and grain, its right corners rounded, the popover's shadow.
private struct MeadowDrawer: ViewModifier {
    @Environment(\.meadow) private var meadow

    func body(content: Content) -> some View {
        let shape = UnevenRoundedRectangle(bottomTrailingRadius: 18, topTrailingRadius: 18)
        content
            .background {
                ZStack {
                    meadow.paper
                    MeadowGrain()
                }
            }
            .clipShape(shape)
            .meadowShadow(meadow.shadowPop, radius: 18)
    }
}

/// The splitter between the list and the pane (`.splitter`): drag it, or with the focus on it the arrow keys, Home
/// and End; a double-click puts the list back to its own width. Its grip shows while it is pointed at, held or
/// focused. Only Tab brings the keyboard to it: the focus a window gives as it opens or the welcome goes, or a click
/// on it, goes back to the list.
struct MeadowSplitter: View {
    let shell: MeadowShell
    @State private var hovering = false
    @State private var start: CGFloat?
    @FocusState private var focused: Bool
    @Environment(\.meadow) private var meadow
    @Environment(\.accessibilityReduceMotion) private var still

    var body: some View {
        Color.clear
            .contentShape(Rectangle())
            .overlay(alignment: .leading) {
                Capsule()
                    .fill(meadow.ink3)
                    .frame(width: 2, height: 44)
                    .offset(x: 3)
                    .opacity(hovering || focused || start != nil ? 0.55 : 0)
                    .animation(still ? nil : .easeOut(duration: 0.12), value: hovering || focused || start != nil)
            }
            .overlay {
                if focused {
                    RoundedRectangle(cornerRadius: 4).stroke(meadow.focus, lineWidth: 2)
                }
            }
            .pointerStyle(.columnResize)
            .onHover { hovering = $0 }
            .gesture(
                DragGesture(minimumDistance: 1, coordinateSpace: .global)
                    .onChanged { drag in start = shell.dragList(from: start, by: drag.translation.width) }
                    .onEnded { _ in start = nil }
            )
            .simultaneousGesture(TapGesture(count: 2).onEnded { shell.resetList() })
            .focusable()
            .focused($focused)
            .focusEffectDisabled()
            .onChange(of: focused) { _, now in
                guard now, !Self.keepsFocus(after: NSApp.currentEvent) else { return }
                focused = false
                if let window = NSApp.keyWindow, let table = ListTable.find(in: window.contentView) {
                    window.makeFirstResponder(table)
                }
            }
            .onKeyPress(.leftArrow) { nudge(-1) }
            .onKeyPress(.rightArrow) { nudge(1) }
            .onKeyPress(.home) {
                shell.setListWidth(ShellLayout.listMin)
                return .handled
            }
            .onKeyPress(.end) {
                shell.setListWidth(ShellLayout.listMax)
                return .handled
            }
            .help("Drag to resize · ←/→ · double-click resets")
            // VoiceOver reads it as what it does, a slider: the list's width shown, a step wider or narrower as the
            // arrow keys move it. A plain element with an adjustable action had no role.
            .accessibilityRepresentation {
                Slider(value: Binding(get: { Double(shell.shownListWidth) },
                                      set: { shell.setListWidth(CGFloat($0)) }),
                       in: Double(ShellLayout.listMin)...Double(ShellLayout.listMax)) {
                    Text("Resize the message list")
                }
                .accessibilityValue("\(Int(shell.shownListWidth)) points wide")
                .accessibilityAdjustableAction { direction in
                    switch direction {
                    case .increment: shell.nudgeList(by: 1)
                    case .decrement: shell.nudgeList(by: -1)
                    @unknown default: break
                    }
                }
            }
    }

    private func nudge(_ steps: Int) -> KeyPress.Result {
        shell.nudgeList(by: steps)
        return .handled
    }

    /// Whether the focus the splitter was given stays: only when Tab or ⇧Tab moved it there.
    static func keepsFocus(after event: NSEvent?) -> Bool {
        event?.type == .keyDown && event?.keyCode == 48
    }
}

/// The account alert under the top bar, in the light's sun: while an account can't connect, until it is closed, over a
/// place that spans the window.
private struct MeadowAccountAlert: View {
    let status: AppStatus

    var body: some View {
        if !status.alerting.isEmpty {
            AccountAlert(status: status)
                .padding(.horizontal, ShellLayout.gap)
                .padding(.bottom, ShellLayout.gap)
        }
    }
}

/// The View menu's part of the shell: the rail's names, focus reading and the light.
struct MeadowCommands: Commands {
    @FocusedValue(\.meadowShell) private var shell
    @FocusedValue(\.mailbox) private var mailbox
    @Environment(\.openWindow) private var openWindow

    var body: some Commands {
        CommandGroup(replacing: .sidebar) {
            MeadowViewMenu(shell: shell, mailbox: mailbox, door: .ofThisApp(openWindow))
        }
    }
}

/// View's own group, in place of the sidebar's: how the list is shown (`MailboxViewItems`), then the rail, focus
/// reading and the light. The window's own, while it is open (`MailWindowDoor`).
struct MeadowViewMenu: View {
    private let focusedShell: MeadowShell?
    private let focusedMailbox: Mailbox?
    private let door: MailWindowDoor

    init(shell: MeadowShell?, mailbox: Mailbox?, door: MailWindowDoor = .alwaysOpen) {
        focusedShell = shell
        focusedMailbox = mailbox
        self.door = door
    }

    var body: some View {
        let open = door.isOpen()
        let shell = open ? focusedShell : nil
        let mailbox = open ? focusedMailbox : nil
        MailboxViewItems(mailbox: mailbox)
        Divider()
        Button(shell?.docked == false ? "Show Mailboxes" : shell?.railOpen == true ? "Hide Mailbox Names"
            : "Show Mailbox Names") {
            shell?.toggleRail()
        }
        .keyboardShortcut("s", modifiers: [.control, .command])
        .disabled(shell == nil)
        Button(shell?.focus == true ? "Leave Focus Reading" : "Focus Reading") { shell?.toggleFocus() }
            .keyboardShortcut(.return, modifiers: .command)
            .disabled(shell == nil || (shell?.focus != true && mailbox?.app?.selection.copy == nil))
        MeadowLightPicker(status: .shared)
        Divider()
    }
}

/// Automatic, Day, Dusk or Night, as the top bar's button and Settings choose it.
private struct MeadowLightPicker: View {
    let status: AppStatus

    var body: some View {
        Picker("Light", selection: Binding(get: { status.theme }, set: { theme in
            status.setPreferences(API.ChangedPreferences(theme: theme))
        })) {
            ForEach(API.Theme.round, id: \.self) { theme in
                Text(theme.name).tag(theme)
            }
        }
        .disabled(status.core == nil)
    }
}
#endif
