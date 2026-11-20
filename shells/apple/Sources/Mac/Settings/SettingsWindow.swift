#if os(macOS)
import SwiftUI

/// MailMind's Settings window, the page's Settings as a Mac's: the accounts, then what the page calls Preferences,
/// split as a Mac splits them. Everything is the server's: each control saves on its own, as the page's does.
///
/// It is drawn as a Meadow dialog (#44): paper with its grain and no title bar, the traffic lights in its head beside
/// its title, its places down the left as the rail lists them, and each place's settings in cards.
struct SettingsWindow: View {
    @Bindable var status: AppStatus
    /// Whether the window's traffic lights stand at the head's left; a test's picture has none.
    var lights = true

    /// The head's height: the title's line and its padding, the traffic lights centred on it.
    static let headHeight: CGFloat = 52
    /// The dialog's size, below the title bar's room, which its paper takes too.
    static let size = CGSize(width: 840, height: 640)

    var body: some View {
        SettingsLayout(status: status, lights: lights, opened: status.core != nil)
    }
}

/// The Settings window's look, whether the core is open yet or not; a test draws it with `opened` on fictional
/// answers.
struct SettingsLayout: View {
    @Bindable var status: AppStatus
    var lights = true
    var opened = true
    @Environment(\.meadow) private var meadow
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            head
            ComposeHairline()
            if !opened {
                ProgressView("Opening MailMind")
                    .controlSize(.small)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                HStack(spacing: 0) {
                    SettingsPlaces(selection: $status.settingsTab)
                    Rectangle().fill(meadow.line).frame(width: 1)
                    pane
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background { ComposePaper() }
        .overlay(alignment: .bottom) { NoticeBanner(status: status) }
        // Esc closes Settings, as ⌘W does, wherever the keyboard is in it.
        .background {
            Button("Close Settings") { dismiss() }
                .keyboardShortcut(.cancelAction)
                .opacity(0)
                .frame(width: 0, height: 0)
                .accessibilityHidden(true)
        }
        // The window is sized from the frame below, which stands in the room under its title bar; the paper takes
        // that room as well, up to the window's top, so it runs from edge to edge with no strip of the window's own
        // colour at its foot.
        .ignoresSafeArea()
        .frame(width: SettingsWindow.size.width, height: SettingsWindow.size.height)
        .meadowWindowChrome(barHeight: SettingsWindow.headHeight, on: lights)
        .navigationTitle(status.settingsTab.title)
    }

    private var head: some View {
        HStack(spacing: 9) {
            MeadowIconView(.sliders).foregroundStyle(meadow.ink3)
            Text("Settings")
                .font(.meadow(.display, 18, .bold))
                .foregroundStyle(meadow.ink)
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 0)
        }
        .padding(.leading, lights ? WindowLights.room : 18)
        .padding(.trailing, 18)
        .frame(height: SettingsWindow.headHeight)
        .background { WindowDragArea() }
    }

    @ViewBuilder private var pane: some View {
        switch status.settingsTab {
        case .accounts: AccountsPane(status: status)
        case .general: GeneralPane(status: status)
        case .notifications: NotificationsPane(status: status)
        case .ai: AIPane(status: status)
        }
    }
}

extension SettingsTab {
    /// The places, in the order the list shows them.
    static let order: [SettingsTab] = [.accounts, .general, .notifications, .ai]

    /// The place's name, as the list and the window's title say it.
    var title: String {
        switch self {
        case .accounts: String(localized: "Accounts")
        case .general: String(localized: "General")
        case .notifications: String(localized: "Notifications")
        case .ai: String(localized: "AI")
        }
    }

    /// The line under the place's title.
    var lede: String {
        switch self {
        case .accounts: String(localized: "The mail MailMind reads, and how each connection stands.")
        case .general: String(localized: "How MailMind looks, how it sends, its keys, when it opens, and your mail kept safe.")
        case .notifications: String(localized: "What may tap you on the shoulder, and when it may not.")
        case .ai: String(localized: "Whether AI writes for you, which service, and what it may spend.")
        }
    }

    var icon: MeadowIcon {
        switch self {
        case .accounts: .at
        case .general: .sliders
        case .notifications: .bell
        case .ai: .wind
        }
    }
}

/// Settings' places down the left (`.nav-i`): each with its icon, the one open in the indigo wash. They have the
/// keyboard as Settings opens, and the arrow keys go from one to the next, as a Mac's settings' sidebar does.
struct SettingsPlaces: View {
    @Binding var selection: SettingsTab
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(SettingsTab.order, id: \.self) { tab in
                SettingsPlace(tab: tab, current: tab == selection) { selection = tab }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 12)
        .frame(width: 200)
        .focusable()
        .focused($focused)
        .focusEffectDisabled()
        // From the column, or from a place that Tab reached: either way the place open moves.
        .onKeyPress(.downArrow) { step(by: 1) }
        .onKeyPress(.upArrow) { step(by: -1) }
        .onAppear { focused = true }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Settings")
    }

    private func step(by: Int) -> KeyPress.Result {
        selection = SettingsTab.step(from: selection, by: by)
        return .handled
    }
}

extension SettingsTab {
    /// The place `by` places from `tab` in the list's order, round from the last to the first.
    static func step(from tab: SettingsTab, by: Int) -> SettingsTab {
        let all = order
        let index = all.firstIndex(of: tab) ?? 0
        return all[((index + by) % all.count + all.count) % all.count]
    }
}

private struct SettingsPlace: View {
    let tab: SettingsTab
    let current: Bool
    let choose: () -> Void
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool
    @State private var hovering = false

    var body: some View {
        Button(action: choose) {
            HStack(spacing: 11) {
                MeadowIconView(tab.icon)
                Text(tab.title).lineLimit(1)
                Spacer(minLength: 0)
            }
            .font(.meadow(.ui, 14, current ? .heavy : .semibold))
            .foregroundStyle(current ? meadow.primaryInk : hovering ? meadow.ink : meadow.ink2)
            .padding(.horizontal, 10)
            .frame(minHeight: 36)
            .background(RoundedRectangle(cornerRadius: 10)
                .fill(current ? meadow.primaryWash : hovering ? meadow.paper3 : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 10)
        .onHover { hovering = $0 }
        .accessibilityAddTraits(current ? .isSelected : [])
    }
}

/// A place's settings: its title in the display face and the line under it, then its cards, scrolling.
struct SettingsPage<Content: View>: View {
    let tab: SettingsTab
    @ViewBuilder let content: () -> Content
    @Environment(\.meadow) private var meadow

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(tab.title)
                        .font(.meadow(.display, 22, .bold))
                        .foregroundStyle(meadow.ink)
                        .accessibilityAddTraits(.isHeader)
                    Text(tab.lede)
                        .font(.meadow(.ui, 13.5))
                        .foregroundStyle(meadow.ink3)
                }
                .padding(.bottom, 4)
                content()
            }
            .padding(.horizontal, 24)
            .padding(.top, 22)
            .padding(.bottom, 32)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollContentBackground(.hidden)
    }
}

/// One setting in a card: its name, and what sets it at the line's end; its help under it.
struct SettingLine<Control: View>: View {
    let name: String
    var help: String?
    @ViewBuilder let control: () -> Control
    @Environment(\.meadow) private var meadow

    init(_ name: String, help: String? = nil, @ViewBuilder control: @escaping () -> Control) {
        self.name = name
        self.help = help
        self.control = control
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 12) {
                Text(name)
                    .font(.meadow(.ui, 14, .semibold))
                    .foregroundStyle(meadow.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                control()
            }
            .frame(minHeight: 32)
            if let help { SettingHelp(help) }
        }
    }
}

/// A switch for a setting, in the light's indigo.
struct SettingSwitch: View {
    let name: String
    var help: String?
    let isOn: Binding<Bool>
    @Environment(\.meadow) private var meadow

    init(_ name: String, help: String? = nil, isOn: Binding<Bool>) {
        self.name = name
        self.help = help
        self.isOn = isOn
    }

    var body: some View {
        SettingLine(name, help: help) {
            Toggle(name, isOn: isOn)
                .toggleStyle(.switch)
                .labelsHidden()
                .tint(meadow.primary)
        }
    }
}

/// A setting chosen from a list, as Meadow's popovers offer one (the composer's From, `.fromsel`): what is chosen on
/// paper with a chevron, and a click opens its menu under it. `parts` is read each time the menu is drawn, so it shows
/// what changes while it is open; `opened` is told each time it opens.
struct SettingChoice: View {
    let label: String
    let title: String
    var width: CGFloat = 300
    var opened: () -> Void = {}
    let parts: () -> [MeadowMenu.Part]
    @State private var shown = false
    @State private var menu = MeadowMenuModel()
    @Environment(\.meadow) private var meadow

    var body: some View {
        Button { shown.toggle() } label: {
            HStack(spacing: 8) {
                Text(title).lineLimit(1)
                MeadowIconView(.down, size: 15).foregroundStyle(meadow.ink3)
            }
            .font(.meadow(.ui, 13.5, .bold))
            .foregroundStyle(meadow.ink)
            .padding(.horizontal, 11)
            .frame(height: 32)
            .background(meadow.paper, in: RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(meadow.line))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .fixedSize()
        .accessibilityLabel(label)
        .accessibilityValue(title)
        .meadowPopover(isPresented: $shown, keys: { menu.key($0) }) {
            MeadowMenu(parts: parts(), width: width, model: menu, close: { shown = false }, label: label)
        }
        .onChange(of: shown) { _, now in
            guard now else { return }
            menu.at = 0
            opened()
        }
    }
}

/// How a connection stands, as the mockup's `.dot`: moss when it is well, sunlight while it is busy, rust when it
/// can't connect, each in a soft ring of its colour.
struct SettingDot: View {
    let colour: Color

    var body: some View {
        Circle()
            .fill(colour)
            .frame(width: 8, height: 8)
            .background(Circle().fill(colour.opacity(0.22)).frame(width: 14, height: 14))
            .frame(width: 14, height: 14)
            .accessibilityHidden(true)
    }
}

/// The help under a setting, as the page's `set-help` lines.
struct SettingHelp: View {
    let text: String
    @Environment(\.meadow) private var meadow

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.meadow(.ui, 12.5))
            .foregroundStyle(meadow.ink3)
            .lineSpacing(MeadowFace.ui.lineSpacing(size: 12.5, lineHeight: 1.5))
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// A secret kept in the login Keychain (an AI provider's API key, the Proton Bridge's password): whether one is set and where
/// the one in use comes from, never what it is; a field to keep a new one, which goes straight to its route and is
/// emptied once sent; and a way to take the kept one out.
struct KeptSecretView: View {
    let label: String
    let prompt: String
    let standing: SecretStanding?
    /// Keep what was typed; the words a refusal gave, or nil.
    let keep: (String) async -> String?
    let remove: () async -> String?
    let removeWarning: String

    @State private var typed = ""
    @State private var working = false
    @State private var problem: String?
    @State private var asking = false
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let standing {
                HStack(spacing: 8) {
                    MeadowIconView(standing.kept ? .lock : .unlock, size: 15)
                    Text(standing.summary)
                }
                .font(.meadow(.ui, 13.5, .bold))
                .foregroundStyle(standing.kept ? meadow.ink : meadow.ink3)
                if let note = standing.note { SettingHelp(note) }
            } else {
                ProgressView().controlSize(.small)
            }
            HStack(spacing: 6) {
                SecureField(label, text: $typed, prompt: Text(prompt))
                    .labelsHidden()
                    .meadowComposeField()
                    .onSubmit { send() }
                    .accessibilityLabel(label)
                Button(standing?.kept == true ? "Replace" : "Keep") { send() }
                    .meadowButton(.primary, small: true)
                    .disabled(typed.isEmpty || working)
                if standing?.kept == true {
                    Button("Remove…") { asking = true }
                        .meadowButton(del: true, small: true)
                        .disabled(working)
                }
            }
            if let problem {
                Text(problem).font(.meadow(.ui, 12.5, .semibold)).foregroundStyle(meadow.rust)
            }
        }
        .confirmationDialog("Remove the \(label) from the Keychain?", isPresented: $asking) {
            Button("Remove", role: .destructive) {
                Task {
                    working = true
                    problem = await remove()
                    working = false
                }
            }
        } message: {
            Text(removeWarning)
        }
    }

    /// The typed secret goes to its route at once, and the field lets go of it whatever the answer.
    private func send() {
        guard !typed.isEmpty, !working else { return }
        let secret = typed
        typed = ""
        working = true
        Task {
            problem = await keep(secret)
            working = false
        }
    }
}

/// The few words on what just happened, for a moment, at the foot of Settings: Meadow's toast, as the window's.
struct NoticeBanner: View {
    let status: AppStatus

    var body: some View {
        VStack {
            if let notice = status.notice {
                MeadowToast(icon: MailboxToast.icon(notice.text, refused: notice.refused), notice.text)
                    .id(notice.id)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .accessibilityAddTraits(.updatesFrequently)
            }
        }
        .padding(.bottom, 20)
        .animation(.snappy(duration: 0.2), value: status.notice)
    }
}
#endif
