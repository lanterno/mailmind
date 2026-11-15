import SwiftUI

// Meadow's sky, its painted scene and the paper's grain (#44). Until they are painted here, the sky is its gradient,
// the scene its sky over a hill, and the grain nothing.

/// The window's sky, behind every panel: the light's gradient, its drifting clouds, and at night its stars.
struct MeadowSky: View {
    @Environment(\.meadow) private var meadow

    var body: some View {
        LinearGradient(stops: [.init(color: meadow.sky1, location: 0), .init(color: meadow.sky2, location: 0.34),
                               .init(color: meadow.sky3, location: 1)],
                       startPoint: .top, endPoint: .bottom)
            .ignoresSafeArea()
            .accessibilityHidden(true)
    }
}

/// The painted scene: sky, sun or moon, clouds, hills, a tree and a cottage, the same picture for the same seed. The
/// brief's header, the empty reading pane, the brief's row and the gate (with its gate) show it.
struct MeadowScene: View {
    /// Which picture: the same seed always draws the same one.
    var seed: Int
    /// The gate's own scene, with the screener's gate in the meadow.
    var gate = false
    /// Where the picture is pinned when it is cropped to fill (the mockup's xMaxYMax slice: its bottom right).
    var alignment: Alignment = .bottomTrailing

    @Environment(\.meadow) private var meadow

    var body: some View {
        ZStack(alignment: .bottom) {
            LinearGradient(colors: [meadow.sky1, meadow.sky2, meadow.sky3], startPoint: .top, endPoint: .bottom)
            Ellipse().fill(meadow.hillMid).frame(height: 80).offset(y: 40)
        }
        .clipped()
        .accessibilityHidden(true)
    }
}

/// The paper's grain: a fine noise over a panel, in the light's grain colour.
struct MeadowGrain: View {
    var body: some View {
        Color.clear.accessibilityHidden(true)
    }
}
