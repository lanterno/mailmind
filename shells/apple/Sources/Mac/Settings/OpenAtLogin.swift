#if os(macOS)
import ServiceManagement
import SwiftUI

/// What opens MailMind at login: the app as a login item of macOS's (`SMAppService.mainApp`), or a test's stand-in.
/// macOS keeps it, not the server: it is this Mac's, not a preference of the mail's.
@MainActor
protocol LoginItem {
    var status: SMAppService.Status { get }
    func register() throws
    func unregister() throws
}

extension SMAppService: LoginItem {}

/// Settings › General's Open at Login: whether macOS opens MailMind as you log in, so mail scheduled for later goes
/// at its time and new mail comes in. macOS may ask for it to be allowed in its own Login Items first.
@MainActor
@Observable
final class OpenAtLogin {
    @ObservationIgnored private let item: any LoginItem
    private(set) var status: SMAppService.Status
    /// What went wrong the last time it was changed, in words.
    private(set) var problem: String?

    init(item: any LoginItem = SMAppService.mainApp) {
        self.item = item
        status = item.status
    }

    /// On once asked for, whether or not macOS has let it yet.
    var isOn: Bool { status == .enabled || status == .requiresApproval }
    /// macOS waits for it to be allowed in System Settings › General › Login Items.
    var needsApproval: Bool { status == .requiresApproval }

    func set(_ on: Bool) {
        do {
            if on { try item.register() } else { try item.unregister() }
            problem = nil
        } catch {
            problem = on
                ? String(localized: "macOS didn’t let MailMind open at login. Allow it in System Settings › General › Login Items.")
                : String(localized: "macOS didn’t take MailMind out of its login items. Remove it in System Settings › General › Login Items.")
        }
        status = item.status
    }

    /// Read again what macOS holds: a person may have changed it in System Settings.
    func refresh() {
        status = item.status
    }

    static let help = String(localized: """
        MailMind opens as you log in to this Mac, so mail you scheduled goes at its time and new mail comes in. It \
        sends and syncs only while it is open.
        """)
}

/// The card in Settings › General that holds it.
struct OpenAtLoginCard: View {
    @State private var model: OpenAtLogin
    @Environment(\.meadow) private var meadow

    init(model: OpenAtLogin = OpenAtLogin()) {
        _model = State(initialValue: model)
    }

    var body: some View {
        DeskSideCard(title: String(localized: "This Mac"), icon: .info) {
            SettingSwitch(String(localized: "Open at login"), help: OpenAtLogin.help,
                          isOn: Binding(get: { model.isOn }, set: { model.set($0) }))
            if model.needsApproval {
                SettingLine(String(localized: "macOS asks you to allow MailMind in Login Items.")) {
                    Button("Open Login Items") { SMAppService.openSystemSettingsLoginItems() }
                        .meadowButton(small: true)
                }
            }
            if let problem = model.problem {
                Text(problem)
                    .font(.meadow(.ui, 12.5, .semibold))
                    .foregroundStyle(meadow.rust)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .onAppear { model.refresh() }
    }
}
#endif
