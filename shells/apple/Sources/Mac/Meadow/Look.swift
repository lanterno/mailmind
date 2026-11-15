import SwiftUI

// Meadow's look (#44): the palette for the light the window is in, read from the environment by every view that paints.

private struct MeadowPaletteKey: EnvironmentKey {
    static let defaultValue = MeadowPalette.day
}

private struct MeadowLightKey: EnvironmentKey {
    static let defaultValue = MeadowLight.day
}

extension EnvironmentValues {
    /// The colours to paint with: the light's, or its high-contrast copy.
    var meadow: MeadowPalette {
        get { self[MeadowPaletteKey.self] }
        set { self[MeadowPaletteKey.self] = newValue }
    }

    /// The light itself: Day, Dusk or Night, for what draws differently in each (the scene's sun, moon and stars).
    var meadowLight: MeadowLight {
        get { self[MeadowLightKey.self] }
        set { self[MeadowLightKey.self] = newValue }
    }
}

extension View {
    /// Paints this view and everything in it in `light`: Meadow's colours, and the system's own light or dark for the
    /// controls Meadow leaves to it (menus, the date picker).
    func meadowLight(_ light: MeadowLight, highContrast: Bool = false) -> some View {
        environment(\.meadow, MeadowPalette.of(light, highContrast: highContrast))
            .environment(\.meadowLight, light)
            .environment(\.colorScheme, light.isDark ? .dark : .light)
    }
}
