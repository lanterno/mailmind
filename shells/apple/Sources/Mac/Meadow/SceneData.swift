import CoreGraphics
import SwiftUI

// What Meadow's painted scenery is made of (#44), ported from the mockup's `scene()`, `cloud()`, `skyClouds()` and
// `rnd()` number for number, so a seed draws the same picture here as on the page. Everything is in the mockup's own
// units: the scene's 1200 by 320 viewBox, the sky's 1000 by 400 starfield and its 3600 by 240 band of clouds.

/// The mockup's seeded generator (`rnd`): Park and Miller's minimal standard, as JavaScript's doubles compute it.
struct MeadowRandom {
    private var state: Int64

    init(seed: Int) { state = Int64(seed) }

    mutating func next() -> Double {
        state = (state * 16807) % 2147483647
        return Double(state) / 2147483647
    }
}

/// A number as JavaScript's `toFixed(digits)` writes it and the SVG reads it back: the mockup rounds every seeded
/// number so, and the same rounding keeps each star and blade where the page puts it.
func meadowFixed(_ value: Double, _ digits: Int) -> Double {
    let power = pow(10, Double(digits))
    let n = (abs(value) * power).rounded(.toNearestOrAwayFromZero)
    return (value < 0 ? -n : n) / power
}

/// One star: where it is, its radius and how bright.
struct MeadowStar: Equatable {
    let x: Double, y: Double, r: Double, opacity: Double
}

/// One blade of grass: from its base, a quadratic curve to its tip.
struct MeadowBlade: Equatable {
    let x: Double, base: Double
    /// The curve's control point and tip, relative to the base.
    let cx: Double, cy: Double, tx: Double, ty: Double
    /// In the lit green (`hill-lit`) or the meadow's.
    let lit: Bool
    let width: Double

    var path: CGPath {
        let path = CGMutablePath()
        path.move(to: CGPoint(x: x, y: base))
        path.addQuadCurve(to: CGPoint(x: x + tx, y: base + ty), control: CGPoint(x: x + cx, y: base + cy))
        return path
    }

    /// What the blade covers, its stroke left out: what CSS's `transform-box: fill-box` sways it about.
    var fillBox: CGRect {
        CGRect(x: min(x, x + tx), y: base + ty, width: abs(tx), height: -ty)
    }
}

/// One firefly: where it glows, and how far into its flicker it starts.
struct MeadowFirefly: Equatable {
    let x: Double, y: Double
    /// The CSS animation's delay, never positive: the flicker starts this far in.
    let delay: Double
}

/// What a seed decides in the scene: its stars, its three swaying groups of grass and its fireflies, drawn from the
/// generator in the mockup's order.
struct MeadowSceneSeeds: Equatable {
    let stars: [MeadowStar]
    let grass: [[MeadowBlade]]
    let fireflies: [MeadowFirefly]

    /// The mockup's `o.seed || 11`: no seed is seed 11.
    init(seed: Int) {
        var random = MeadowRandom(seed: seed == 0 ? 11 : seed)
        var stars: [MeadowStar] = []
        for _ in 0..<70 {
            let x = meadowFixed(random.next() * 1200, 0), y = meadowFixed(random.next() * 170, 0)
            let r = meadowFixed(random.next() * 1.3 + 0.3, 2), opacity = meadowFixed(random.next() * 0.6 + 0.3, 2)
            stars.append(MeadowStar(x: x, y: y, r: r, opacity: opacity))
        }
        var grass: [[MeadowBlade]] = []
        for _ in 0..<3 {
            var blades: [MeadowBlade] = []
            for _ in 0..<46 {
                let x = random.next() * 1200, h = 10 + random.next() * 22, lean = (random.next() - 0.4) * 10
                let base = 322 - random.next() * 8
                let lit = random.next() > 0.5
                let width = meadowFixed(1.2 + random.next(), 1)
                blades.append(MeadowBlade(x: meadowFixed(x, 1), base: meadowFixed(base, 1),
                                          cx: meadowFixed(lean * 0.4, 1), cy: meadowFixed(-h * 0.55, 1),
                                          tx: meadowFixed(lean, 1), ty: meadowFixed(-h, 1), lit: lit, width: width))
            }
            grass.append(blades)
        }
        var fireflies: [MeadowFirefly] = []
        for _ in 0..<12 {
            let delay = meadowFixed(-random.next() * 4, 1)
            let x = meadowFixed(560 + random.next() * 620, 0), y = meadowFixed(250 + random.next() * 60, 0)
            fireflies.append(MeadowFirefly(x: x, y: y, delay: delay))
        }
        self.stars = stars
        self.grass = grass
        self.fireflies = fireflies
    }

    /// The sky's starfield: 110 stars from seed 5, in its 1000 by 400 viewBox.
    static let skyStars: [MeadowStar] = {
        var random = MeadowRandom(seed: 5)
        return (0..<110).map { _ in
            let x = meadowFixed(random.next() * 1000, 0), y = meadowFixed(random.next() * 400, 0)
            let r = meadowFixed(random.next() * 1.1 + 0.3, 2), opacity = meadowFixed(random.next() * 0.6 + 0.25, 2)
            return MeadowStar(x: x, y: y, r: r, opacity: opacity)
        }
    }()
}

/// A cloud as `cloud()` places one: its puffs moved to (x, y), scaled, at an opacity.
struct MeadowCloud {
    let x: Double, y: Double, scale: Double, opacity: Double

    /// The mockup's eleven puffs, (dx, dy, r) about the cloud's middle.
    static let puffs: [(Double, Double, Double)] = [
        (-74, 2, 30), (-40, -8, 38), (0, -14, 46), (44, -6, 36), (80, 4, 26), (-30, -46, 32), (10, -60, 40),
        (48, -40, 28), (-4, -96, 30), (24, -88, 25), (-22, -80, 22),
    ]
    /// Every cloud is cut flat along this line, in its own units: its clip path's bottom edge.
    static let floor = 24.0

    /// The four clouds that drift behind the scene's hills.
    static let scene = [
        MeadowCloud(x: 930, y: 178, scale: 1.3, opacity: 1), MeadowCloud(x: 1130, y: 196, scale: 0.8, opacity: 0.95),
        MeadowCloud(x: 700, y: 66, scale: 0.34, opacity: 0.9), MeadowCloud(x: 760, y: 150, scale: 0.22, opacity: 0.8),
    ]
    /// The five clouds of half the sky's band, each half at .9 of its own opacity.
    static let sky = [
        MeadowCloud(x: 120, y: 118, scale: 0.42, opacity: 1), MeadowCloud(x: 520, y: 96, scale: 0.3, opacity: 0.8),
        MeadowCloud(x: 860, y: 128, scale: 0.5, opacity: 1), MeadowCloud(x: 1260, y: 90, scale: 0.32, opacity: 0.85),
        MeadowCloud(x: 1620, y: 132, scale: 0.44, opacity: 1),
    ]
}

/// The scene's fixed shapes, as the mockup's markup writes them (SVG path data, read by `meadowPath`).
enum MeadowShapes {
    static let mountain = "M0 205 C 90 186 150 170 230 178 C 320 186 360 160 450 150 C 540 140 600 172 690 176 "
        + "C 780 180 830 158 920 168 C 1010 178 1080 192 1200 184 L1200 320 L0 320Z"
    static let hillFar = "M0 230 C 140 212 260 208 380 216 C 500 224 600 236 720 230 C 840 224 940 206 1060 212 "
        + "C 1120 215 1170 222 1200 224 L1200 320 L0 320Z"
    static let hillMid = "M0 260 C 120 242 240 238 360 246 C 480 254 560 262 680 258 C 800 254 860 244 960 242 "
        + "C 1060 240 1140 252 1200 250 L1200 320 L0 320Z"
    static let hillNear = "M0 302 C 160 286 320 278 480 284 C 640 290 760 302 900 294 C 1020 287 1120 278 1200 282 "
        + "L1200 320 L0 320Z"
    /// The cottage, about (842, 249), and the path down from it.
    static let house = CGPoint(x: 842, y: 249)
    static let roof = "M-18 -14 L1 -28 L20 -14z"
    static let lane = "M848 252 C 820 268 760 282 702 298 C 668 308 646 316 630 322"
    /// The tree, about (1012, 242); its canopy's circles about (0, -62) from there.
    static let tree = CGPoint(x: 1012, y: 242)
    static let trunk = "M-5 1 C -4 -16 -7 -28 -11 -38 L -4 -39 C 0 -28 3 -16 6 1Z"
    static let branches = "M-6 -24 C -16 -30 -22 -36 -26 -44 M0 -28 C 8 -36 16 -40 26 -44"
    static let canopyOffset = CGPoint(x: 0, y: -62)
    static let canopy: [(Double, Double, Double)] = [
        (-44, 10, 22), (-26, -10, 28), (2, -22, 33), (30, -8, 27), (48, 10, 21), (-8, 6, 31), (22, 14, 24),
        (-30, 16, 19), (8, -40, 22),
    ]
    /// The screener's gate, about (600, 262).
    static let gate = CGPoint(x: 600, y: 262)
    static let gateRail = "M-120 18 C -60 14 60 14 120 18"
    static let fencePosts = "M-150 22v-30M-110 20v-28M-80 19v-27M80 19v-27M110 20v-28M150 22v-30"
    static let fenceRails = "M-152 0h70M80 0h72M-152 10h70M80 10h72"
    static let gateArch = "M-40 -46 Q 0 -66 40 -46"
    static let gateBars = "M-24 -26h16M-24 -10h16M-24 6h16M8 -26h16M8 -10h16M8 6h16"
    static let gateStiles = "M-21 -30v40M-11 -30v40M11 -30v40M21 -30v40"
    /// The two birds.
    static let birds = "M820 92q5-5 10 0q5-5 10 0M846 104q4-4 8 0q4-4 8 0"
    /// The sun or the moon, and the moon's bite.
    static let disc = CGPoint(x: 1080, y: 70)
    static let bite = CGPoint(x: 1091, y: 63)
}

/// A path from the scenery's SVG path data, which is fixed and known to read: the tests read every shape.
func meadowPath(_ data: String) -> CGPath {
    do {
        return try SVGPath.parse(data).cgPath
    } catch {
        preconditionFailure("The scenery's path data doesn't read: \(error)")
    }
}

/// How a picture of `content` units fills a frame and is cut to it, as SVG's `preserveAspectRatio="… slice"` does:
/// scaled until it covers the frame, then pinned where `alignment` says (xMaxYMax is `.bottomTrailing`).
struct MeadowCrop: Equatable {
    /// Points per unit of the picture.
    let scale: Double
    /// Where the picture's origin lands in the frame, in points (never positive).
    let origin: CGPoint

    init(content: CGSize, frame: CGSize, alignment: Alignment) {
        let scale = max(frame.width / content.width, frame.height / content.height)
        let spare = CGSize(width: frame.width - content.width * scale, height: frame.height - content.height * scale)
        let x: Double = switch alignment.horizontal {
        case .leading: 0
        case .trailing: 1
        default: 0.5
        }
        let y: Double = switch alignment.vertical {
        case .top: 0
        case .bottom: 1
        default: 0.5
        }
        self.scale = scale.isFinite ? scale : 0
        self.origin = CGPoint(x: spare.width * x, y: spare.height * y)
    }

    /// The scene's 1200 by 320 viewBox in a frame.
    static func scene(_ frame: CGSize, _ alignment: Alignment) -> MeadowCrop {
        MeadowCrop(content: CGSize(width: 1200, height: 320), frame: frame, alignment: alignment)
    }
}
