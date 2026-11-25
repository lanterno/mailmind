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
    /// Opens System Settings at its Login Items, where macOS asks for one to be allowed.
    func openSettings()
}

extension LoginItem {
    /// A stand-in opens nothing.
    func openSettings() {}
}

extension SMAppService: LoginItem {
    func openSettings() {
        SMAppService.openSystemSettingsLoginItems()
    }
}

/// Settings › General's Open at Login: whether macOS opens MailMind as you log in, so mail scheduled for later goes
/// at its time and new mail comes in. macOS may ask for it to be allowed in its own Login Items first.
///
/// Only a person's run has it (`CoreHome.isAPersonsRun`). Every Release build is `io.github.lanterno.mailmind`, so a
/// demo's or a test's run would add its own build to the person's login items, to open with no home as they log in:
/// such a run has no item, never asks macOS, and says why under the switch.
@MainActor
@Observable
final class OpenAtLogin {
    @ObservationIgnored private let item: (any LoginItem)?
    private(set) var status: SMAppService.Status
    /// What went wrong the last time it was changed, in words.
    private(set) var problem: String?

    /// `item` nil: a run that isn't a person's, which has no switch.
    init(item: (any LoginItem)?) {
        self.item = item
        status = item?.status ?? .notRegistered
    }

    /// The app's login item (`AppSeams.loginItem`): macOS's in a person's run; in any other none, and
    /// `SMAppService` is never asked.
    convenience init() {
        self.init(item: AppSeams.loginItem)
    }

    /// The login item a run with `environment` has (`SystemLoginItem.forThisProcess`).
    convenience init(_ environment: [String: String]) {
        self.init(item: SystemLoginItem.forThisProcess(environment))
    }

    /// Whether this run has the switch.
    var available: Bool { item != nil }
    /// On once asked for, whether or not macOS has let it yet.
    var isOn: Bool { status == .enabled || status == .requiresApproval }
    /// macOS waits for it to be allowed in System Settings › General › Login Items.
    var needsApproval: Bool { status == .requiresApproval }

    func set(_ on: Bool) {
        guard let item else { return }
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
        guard let item else { return }
        status = item.status
    }

    /// System Settings at its Login Items.
    func openSettings() {
        item?.openSettings()
    }

    static let help = String(localized: """
        MailMind opens as you log in to this Mac, so mail you scheduled goes at its time and new mail comes in. It \
        sends and syncs only while it is open.
        """)

    /// What a demo or test run says under the switch it doesn't have, as read only's card says it.
    static let notAvailable = String(localized: """
        This demo or test run never opens at login. Open at login is a switch here when you open MailMind yourself.
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
                .disabled(!model.available)
            if !model.available { SettingHelp(OpenAtLogin.notAvailable) }
            if model.needsApproval {
                SettingLine(String(localized: "macOS asks you to allow MailMind in Login Items.")) {
                    Button("Open Login Items") { model.openSettings() }
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
