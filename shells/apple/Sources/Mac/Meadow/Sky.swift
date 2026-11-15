import SwiftUI

// Meadow's sky, its painted scene and the paper's grain (#44), as the mockup paints them: its `.sky`, `scene()` and
// `--grain`. What stands still is SwiftUI's gradients and shapes, which cost no bitmap of their own (a Canvas would
// keep one the size of its frame). What moves is Core Animation's layers (SceneLayers.swift), whose motion runs off
// the main thread and stops when Reduce Motion is on. Drawn without a window, as ImageRenderer draws, it is the same
// picture at rest.

/// The window's sky, behind every panel: the light's gradient, the drifting band of clouds, and at night its stars.
struct MeadowSky: View {
    @Environment(\.meadow) private var meadow
    @Environment(\.displayScale) private var displayScale
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var live = false

    var body: some View {
        LinearGradient(stops: [.init(color: meadow.sky1, location: 0), .init(color: meadow.sky2, location: 0.34),
                               .init(color: meadow.sky3, location: 1)],
                       startPoint: .top, endPoint: .bottom)
            .overlay {
                GeometryReader { geometry in
                    let colors = MeadowSceneColors(meadow)
                    #if os(macOS)
                    if live {
                        MeadowSkyLayers(colors: colors, size: geometry.size, displayScale: displayScale,
                                        moving: !reduceMotion)
                    } else {
                        MeadowSkyAtRest(colors: colors, size: geometry.size, displayScale: displayScale)
                    }
                    #else
                    MeadowSkyAtRest(colors: colors, size: geometry.size, displayScale: displayScale)
                    #endif
                }
            }
            #if os(macOS)
            .background { MeadowLiveProbe(live: $live).frame(width: 0, height: 0) }
            #endif
            .ignoresSafeArea()
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}

/// The sky's stars and its first window of clouds, where the drift starts.
private struct MeadowSkyAtRest: View {
    let colors: MeadowSceneColors
    let size: CGSize
    let displayScale: Double

    var body: some View {
        let box = CGSize(width: size.width, height: size.height * 0.6)
        let crop = MeadowCrop(content: CGSize(width: 1000, height: 400), frame: box, alignment: .top)
        let (sx, sy) = MeadowSkyLayer.bandScale(size.width)
        let pixels = MeadowArt.quantized(max(sx, sy) * displayScale)
        ZStack(alignment: .topLeading) {
            if colors.stars > 0 {
                // The starfield's box cuts off what falls below it.
                let shown = MeadowSceneSeeds.skyStars.filter { crop.origin.y + $0.y * crop.scale < box.height }
                ForEach(shown.indices, id: \.self) { index in
                    let star = shown[index], r = star.r * crop.scale
                    Circle().fill(.white.opacity(star.opacity))
                        .frame(width: 2 * r, height: 2 * r)
                        .position(x: crop.origin.x + star.x * crop.scale, y: crop.origin.y + star.y * crop.scale)
                }
                .opacity(colors.stars)
            }
            ForEach(MeadowCloud.sky.indices, id: \.self) { index in
                let cloud = MeadowCloud.sky[index]
                let (image, rect) = MeadowArt.skyCloud(index, colors, pixels: pixels)
                Image(decorative: image, scale: 1).resizable()
                    .frame(width: rect.width * sx, height: rect.height * sy)
                    .position(x: (cloud.x + rect.midX) * sx, y: (cloud.y + rect.midY) * sy)
                    .opacity(0.9 * cloud.opacity)
            }
        }
        .frame(width: size.width, height: size.height, alignment: .topLeading)
    }
}

/// The painted scene: sky, sun or moon, clouds, hills, a tree and a cottage, the same picture for the same seed. The
/// brief's header, the empty reading pane, the brief's row and the gate (with its gate) show it.
struct MeadowScene: View {
    /// Which picture: the same seed always draws the same one.
    var seed: Int
    /// The gate's own scene, with the screener's gate in the meadow.
    var gate = false
    /// Where the picture is pinned when it is cropped to fill (the mockup's xMaxYMax slice: its bottom right). The
    /// gate's header is the mockup's xMidYMax: `.bottom`.
    var alignment: Alignment = .bottomTrailing

    @Environment(\.meadow) private var meadow
    @Environment(\.displayScale) private var displayScale
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var live = false
    @State private var picture = MeadowScenePicture()

    var body: some View {
        GeometryReader { geometry in
            let plan = MeadowScenePlan(seed: seed, gate: gate, colors: MeadowSceneColors(meadow),
                                       size: geometry.size, alignment: alignment, displayScale: displayScale)
            ZStack {
                MeadowSceneBack(plan: plan).equatable()
                // The scene's fine texture, which the mockup multiplies over all of it: here over the sky, and drawn
                // into the clouds' and hills' own pictures.
                Image(decorative: MeadowNoise.sceneTexture, scale: displayScale).resizable(resizingMode: .tile)
                #if os(macOS)
                if live {
                    MeadowSceneLayers(plan: plan, moving: !reduceMotion)
                } else {
                    picture.image(plan)
                }
                #else
                picture.image(plan)
                #endif
            }
        }
        #if os(macOS)
        .background { MeadowLiveProbe(live: $live).frame(width: 0, height: 0) }
        .onChange(of: live) { picture.forget() }
        #endif
        .clipped()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// The scene's sky, behind its clouds and hills: the gradient, the sun's glow, and the sun, or the stars and the moon.
private struct MeadowSceneBack: View, Equatable {
    let plan: MeadowScenePlan

    /// A rect of the scene, in points.
    private func shown(_ rect: CGRect) -> CGRect {
        let crop = plan.crop
        return CGRect(x: crop.origin.x + rect.minX * crop.scale, y: crop.origin.y + rect.minY * crop.scale,
                      width: rect.width * crop.scale, height: rect.height * crop.scale)
    }

    var body: some View {
        let c = plan.colors
        let crop = plan.crop
        let disc = MeadowShapes.disc
        let scene = shown(CGRect(x: 0, y: 0, width: 1200, height: 320))
        ZStack(alignment: .topLeading) {
            Rectangle()
                .fill(LinearGradient(stops: [.init(color: c.sky1, location: 0), .init(color: c.sky2, location: 0.55),
                                             .init(color: c.sky3, location: 1)],
                                     startPoint: .top, endPoint: .bottom))
                .placed(scene)
            Rectangle()
                .fill(RadialGradient(colors: [c.discGlow, c.discGlow.opacity(0)],
                                     center: UnitPoint(x: disc.x / 1200, y: disc.y / 320),
                                     startRadius: 0, endRadius: 380 * crop.scale))
                .placed(scene)
            if c.stars > 0 {
                let stars = MeadowSceneSeeds(seed: plan.seed).stars
                ForEach(stars.indices, id: \.self) { index in
                    let star = stars[index]
                    Circle().fill(.white.opacity(star.opacity))
                        .placed(shown(CGRect(x: star.x - star.r, y: star.y - star.r, width: 2 * star.r,
                                             height: 2 * star.r)))
                }
                .opacity(c.stars)
            }
            if c.sunny > 0 {
                Circle().fill(c.disc)
                    .placed(shown(CGRect(x: disc.x - 30, y: disc.y - 30, width: 60, height: 60)))
                    .opacity(c.sunny)
            }
            if c.stars > 0 {
                ZStack(alignment: .topLeading) {
                    Circle()
                        .fill(RadialGradient(stops: [.init(color: c.discGlow, location: 0.25),
                                                     .init(color: c.discGlow.opacity(0), location: 1)],
                                             center: .center, startRadius: 0, endRadius: 70 * crop.scale))
                        .placed(shown(CGRect(x: disc.x - 70, y: disc.y - 70, width: 140, height: 140)))
                    Path(Self.crescent)
                        .applying(CGAffineTransform(a: crop.scale, b: 0, c: 0, d: crop.scale, tx: crop.origin.x,
                                                    ty: crop.origin.y))
                        .fill(c.disc)
                }
                .opacity(c.stars)
            }
        }
        .frame(width: plan.size.width, height: plan.size.height, alignment: .topLeading)
    }

    /// The moon: its disc, less the bite the mockup's mask takes out of it.
    private static let crescent: CGPath = {
        let disc = MeadowShapes.disc, bite = MeadowShapes.bite
        return CGPath(ellipseIn: CGRect(x: disc.x - 22, y: disc.y - 22, width: 44, height: 44), transform: nil)
            .subtracting(CGPath(ellipseIn: CGRect(x: bite.x - 19, y: bite.y - 19, width: 38, height: 38),
                                transform: nil))
    }()
}

private extension View {
    /// This view laid over `rect` of its parent, in points.
    func placed(_ rect: CGRect) -> some View {
        frame(width: rect.width, height: rect.height).position(x: rect.midX, y: rect.midY)
    }
}

/// The scene's front at rest as one image, made once for a plan and kept while the view lasts.
@MainActor
final class MeadowScenePicture {
    private var plan: MeadowScenePlan?
    private var made: CGImage?

    func image(_ plan: MeadowScenePlan) -> some View {
        if plan != self.plan {
            self.plan = plan
            made = MeadowSceneLayer.picture(plan)
        }
        return Group {
            if let made { Image(decorative: made, scale: plan.displayScale) } else { Color.clear }
        }
    }

    func forget() {
        plan = nil
        made = nil
    }
}

/// The paper's grain: a fine noise over a panel, in the light's grain colour, tiled from one 180-point image.
struct MeadowGrain: View {
    @Environment(\.meadow) private var meadow
    @Environment(\.displayScale) private var displayScale

    var body: some View {
        let scale = max(1, Int(displayScale.rounded()))
        Image(decorative: MeadowNoise.paper(meadow.grain, scale: scale), scale: Double(scale))
            .resizable(resizingMode: .tile)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}
