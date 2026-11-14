import SwiftUI

#if os(macOS)
import Combine
import Sparkle

/// Updates through Sparkle, from the appcast on the repo's latest GitHub release (docs/releasing.md). Only a build
/// `just apple release` made checks: a debug build, `just apple build`'s, a test and a demo never ask the network for
/// an update, and neither does a build whose public key is still the placeholder.
@MainActor
final class Updates {
    static let shared = Updates()

    /// Sparkle's updater and its standard windows, held for the app's life. Nil when this build doesn't check.
    let controller: SPUStandardUpdaterController?

    private init() {
        controller = Self.checks(Bundle.main.infoDictionary ?? [:])
            ? SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: nil, userDriverDelegate: nil)
            : nil
    }

    /// Whether a build with this Info.plist checks for updates: one the release recipe made, with a real key in it.
    /// Sparkle asks the person before its first automatic check, and checks once a day after that if they agree.
    nonisolated static func checks(_ info: [String: Any]) -> Bool {
        #if DEBUG
        return false
        #else
        guard info["MailMindChecksForUpdates"] as? String == "YES" else { return false }
        // An Ed25519 public key is 32 bytes. The placeholder isn't base64 at all.
        guard let key = info["SUPublicEDKey"] as? String, Data(base64Encoded: key)?.count == 32 else { return false }
        return true
        #endif
    }
}

/// "Check for Updates…" in the app menu, after About, in a build that checks.
struct UpdateCommands: Commands {
    var body: some Commands {
        CommandGroup(after: .appInfo) {
            if let updater = Updates.shared.controller?.updater {
                CheckForUpdates(updater: updater)
            }
        }
    }
}

/// The menu item, greyed while Sparkle can't start a check (one is already running).
private struct CheckForUpdates: View {
    let updater: SPUUpdater
    @StateObject private var state: CheckState

    init(updater: SPUUpdater) {
        self.updater = updater
        _state = StateObject(wrappedValue: CheckState(updater: updater))
    }

    var body: some View {
        Button("Check for Updates…") { updater.checkForUpdates() }
            .disabled(!state.canCheck)
    }
}

@MainActor
private final class CheckState: ObservableObject {
    @Published var canCheck = false

    init(updater: SPUUpdater) {
        // Sparkle changes this on the main thread.
        updater.publisher(for: \.canCheckForUpdates).assign(to: &$canCheck)
    }
}
#else
/// Updates on iPhone and iPad come from the App Store, so there is no menu command.
struct UpdateCommands: Commands {
    var body: some Commands { EmptyCommands() }
}
#endif
