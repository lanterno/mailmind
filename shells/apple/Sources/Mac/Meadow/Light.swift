import SwiftUI

// The light's round (#44): the top bar's button goes Day, Dusk, Night, then Day again, and saves the `theme` preference
// the page and the app share, as Settings saves one.

extension MeadowLight {
    /// The light after this one, as the top bar's button goes round.
    var next: MeadowLight {
        switch self {
        case .day: .dusk
        case .dusk: .night
        case .night: .day
        }
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

#if os(macOS)
extension AppStatus {
    /// The light the person chose, Day until the server has said.
    var light: MeadowLight { preferences.map { MeadowLight($0.theme) } ?? .day }

    /// Go on to the next light, here at once and on the server right after.
    @discardableResult
    func nextLight() -> Task<Void, Never>? {
        setPreferences(API.ChangedPreferences(theme: light.next.theme))
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
