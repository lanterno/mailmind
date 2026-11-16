import SwiftUI

// The light's round (#44): the top bar's button goes Automatic, Day, Dusk, Night, then Automatic again, and saves the
// `theme` preference the page and the app share, as Settings saves one. Automatic is Night while macOS is in Dark Mode,
// and else the time of day's: Day from 6, Dusk from 17, Night from 21, the hours the page's lib/light.ts keeps too.

extension MeadowLight {
    /// The light Automatic is in: Night while the system is dark, else the hour's.
    static func automatic(systemDark: Bool, hour: Int) -> MeadowLight {
        if systemDark || hour >= 21 || hour < 6 { return .night }
        return hour >= 17 ? .dusk : .day
    }

    /// What the top bar's button shows while the window is in this light (the mockup's `#themeB`).
    var icon: MeadowIcon {
        switch self {
        case .day: .sun
        case .dusk: .dusk
        case .night: .moon
        }
    }

    /// Its name, as the button says it.
    var name: String {
        switch self {
        case .day: String(localized: "Day")
        case .dusk: String(localized: "Dusk")
        case .night: String(localized: "Night")
        }
    }

    /// The preference that keeps it.
    var theme: API.Theme { API.Theme(rawValue: rawValue) ?? .day }
}

extension API.Theme {
    /// The choices in the order the button goes round them and the pickers list them.
    static let round: [API.Theme] = [.auto, .day, .dusk, .night]

    /// The choice after this one, as the top bar's button goes round.
    var next: API.Theme {
        let at = Self.round.firstIndex(of: self) ?? 0
        return Self.round[(at + 1) % Self.round.count]
    }

    /// Its name, as the pickers and the button say it.
    var name: String {
        switch self {
        case .auto: String(localized: "Automatic")
        case .day: String(localized: "Day")
        case .dusk: String(localized: "Dusk")
        case .night: String(localized: "Night")
        }
    }

    /// The light it paints in, with the system dark or not, at this hour.
    func light(systemDark: Bool, hour: Int) -> MeadowLight {
        self == .auto ? .automatic(systemDark: systemDark, hour: hour) : MeadowLight(self)
    }
}

#if os(macOS)
extension AppStatus {
    /// The theme the person chose, Automatic until the server has said: a fresh install's.
    var theme: API.Theme { preferences?.theme ?? .auto }

    /// The light the window is in: the one chosen, or Automatic's for the system's appearance and the minute.
    var light: MeadowLight {
        theme.light(systemDark: systemDark, hour: Calendar.current.component(.hour, from: now))
    }

    /// Go on to the next choice, here at once and on the server right after.
    @discardableResult
    func nextLight() -> Task<Void, Never>? {
        setPreferences(API.ChangedPreferences(theme: theme.next))
    }

    /// Follow macOS's appearance from now on, for Automatic: Dark Mode as the system sets it, which the windows' own
    /// light doesn't change (they set only their own appearance). The app's status does this once, as it starts.
    func followSystemAppearance() {
        guard appearanceChanged == nil else { return }
        systemDark = Self.systemIsDark()
        appearanceChanged = DistributedNotificationCenter.default().addObserver(
            forName: Notification.Name("AppleInterfaceThemeChangedNotification"), object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.systemDark = Self.systemIsDark() }
        }
    }

    /// Whether macOS is in Dark Mode now, as the system's own setting says.
    nonisolated static func systemIsDark() -> Bool {
        UserDefaults.standard.string(forKey: "AppleInterfaceStyle") == "Dark"
    }
}

/// A window in the light a person chose, and its high contrast: Meadow's colours for SwiftUI, and Night's dark for
/// what AppKit draws (the list's rows, menus, the date picker), which reads the window's appearance.
struct MeadowLit: ViewModifier {
    var status = AppStatus.shared

    func body(content: Content) -> some View {
        let light = status.light
        content
            .meadowLight(light, highContrast: status.preferences?.highContrast ?? false)
            .preferredColorScheme(light.isDark ? .dark : .light)
    }
}

extension View {
    /// Paint this window in the light the person chose.
    func meadowLit(_ status: AppStatus = .shared) -> some View {
        modifier(MeadowLit(status: status))
    }
}
#endif
