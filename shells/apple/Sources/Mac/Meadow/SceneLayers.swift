import QuartzCore
import SwiftUI
#if os(macOS)
import AppKit
#endif

// Meadow's scenery as Core Animation layers (#44). The window shows these layers, and Core Animation runs the
// scenery's motion on its own, so nothing is drawn again while the grass sways or the clouds drift. A picture made
// without a window (ImageRenderer) gets the same layers drawn once, at rest.

/// What a scene is drawn from.
struct MeadowScenePlan: Equatable {
    var seed: Int
    var gate: Bool
    var colors: MeadowSceneColors
    var size: CGSize
    var alignment: Alignment
    /// Pixels a point on the screen it is shown on.
    var displayScale: Double

    var crop: MeadowCrop { .scene(size, alignment) }
    /// Pixels a unit of the scene.
    var pixels: Double { crop.scale * displayScale }
}

/// The scene's front, each part a layer of its own so Core Animation can move it: the drifting clouds, the hills with
/// the cottage or the gate and the tree, the grass, the birds and the fireflies. The sky behind them is SwiftUI's
/// (`MeadowSceneBack`), which costs no layer of its own.
final class MeadowSceneLayer: CALayer {
    private var built: (seed: Int, gate: Bool, colors: MeadowSceneColors)?
    private var plan: MeadowScenePlan?
    private var moving = false
    private var drawnAt = 0.0

    private let world = CALayer()
    private let clouds = CALayer(), middle = CALayer(), bird = CAShapeLayer()
    private var cloudLayers: [CALayer] = []
    private var grass: [CALayer] = []
    private var fireflies: [CALayer] = []
    /// Every vector layer, to draw at the scene's resolution.
    private var vectors: [CALayer] = []

    override init() {
        super.init()
        masksToBounds = true
        world.anchorPoint = .zero
        world.bounds = CGRect(x: 0, y: 0, width: 1200, height: 320)
        addSublayer(world)
    }

    override init(layer: Any) { super.init(layer: layer) }
    required init?(coder: NSCoder) { fatalError("not from a coder") }

    /// Shows `plan`, its motion running if `moving`.
    func apply(_ plan: MeadowScenePlan, moving: Bool) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        defer { CATransaction.commit() }
        if built == nil || built! != (plan.seed, plan.gate, plan.colors) {
            // The clouds and the birds outlive a rebuild, so their motion stops with the rest and starts again below.
            still()
            build(plan)
            drawnAt = 0
            self.moving = false
        }
        if self.plan != plan { place(plan) }
        if moving != self.moving {
            if moving { animate(plan) } else { still() }
            self.moving = moving
        }
        self.plan = plan
    }

    // MARK: Building

    private func build(_ plan: MeadowScenePlan) {
        let c = plan.colors
        let seeds = MeadowSceneSeeds(seed: plan.seed)
        built = (plan.seed, plan.gate, c)
        world.sublayers = nil
        vectors = []

        clouds.sublayers = nil
        cloudLayers = MeadowCloud.scene.map { cloud in
            let layer = CALayer()
            layer.opacity = Float(cloud.opacity)
            clouds.addSublayer(layer)
            return layer
        }
        clouds.frame = world.bounds
        middle.contentsGravity = .resize
        world.addSublayer(clouds)
        world.addSublayer(middle)

        grass = seeds.grass.map { blades in
            let box = blades.reduce(CGRect.null) { $0.union($1.fillBox) }
            let group = CALayer()
            group.bounds = box
            group.anchorPoint = CGPoint(x: 0.5, y: 1)
            group.position = CGPoint(x: box.midX, y: box.maxY)
            for blade in blades {
                let shape = Self.stroke(blade.path, (blade.lit ? c.hillLit : c.meadow).meadowCG, width: blade.width)
                group.addSublayer(shape)
                vectors.append(shape)
            }
            world.addSublayer(group)
            return group
        }

        let birds = Self.stroke(meadowPath(MeadowShapes.birds), c.bird.meadowCG, width: 1.6)
        bird.bounds = birds.bounds
        bird.position = birds.position
        bird.path = birds.path
        bird.strokeColor = birds.strokeColor
        bird.fillColor = nil
        bird.lineWidth = 1.6
        bird.lineCap = .round
        bird.opacity = 0.55
        world.addSublayer(bird)
        vectors.append(bird)

        fireflies = c.glow > 0 ? seeds.fireflies.map { fly in
            let layer = CALayer()
            layer.bounds = CGRect(x: 0, y: 0, width: 18, height: 18)
            layer.position = CGPoint(x: fly.x, y: fly.y)
            layer.opacity = Float(c.glow)
            world.addSublayer(layer)
            return layer
        } : []
    }

    /// A stroked path's layer, its bounds just the stroke's: a shape layer keeps a bitmap of its bounds.
    private static func stroke(_ path: CGPath, _ color: CGColor, width: Double) -> CAShapeLayer {
        let shape = CAShapeLayer()
        let box = path.boundingBoxOfPath.insetBy(dx: -width, dy: -width)
        // Its bounds start where the stroke does, so the path is drawn in the scene's own units.
        shape.bounds = box
        shape.position = CGPoint(x: box.midX, y: box.midY)
        shape.path = path
        shape.strokeColor = color
        shape.fillColor = nil
        shape.lineWidth = width
        shape.lineCap = .round
        return shape
    }

    // MARK: Placing

    private func place(_ plan: MeadowScenePlan) {
        bounds.size = plan.size
        let crop = plan.crop
        world.position = crop.origin
        world.transform = CATransform3DMakeScale(crop.scale, crop.scale, 1)

        // A resize redraws nothing until the scene's size has changed by a step: the vector layers keep their
        // resolution and the bitmaps their pixels, shown a little scaled.
        let pixels = MeadowArt.quantized(plan.pixels)
        guard pixels != drawnAt, plan.pixels > 0 else { return }
        drawnAt = pixels
        for layer in vectors { layer.contentsScale = pixels }
        let c = plan.colors
        middle.contents = MeadowArt.middle(c, gate: plan.gate, pixels: pixels)
        middle.frame = CGRect(x: -4, y: MeadowArt.middleTop, width: 1208, height: 320 - MeadowArt.middleTop)
        for (index, layer) in cloudLayers.enumerated() {
            let (image, rect) = MeadowArt.sceneCloud(index, c, pixels: pixels)
            layer.contents = image
            layer.frame = rect
        }
        let (glow, rect) = MeadowArt.firefly(pixels: pixels)
        for layer in fireflies {
            layer.contents = glow
            layer.bounds = CGRect(origin: .zero, size: rect.size)
        }
    }

    // MARK: Motion

    /// The mockup's CSS animations, as Core Animation's: each runs in the render server, at the few frames a second
    /// its slow motion needs.
    private func animate(_ plan: MeadowScenePlan) {
        let drift = CABasicAnimation(keyPath: "transform.translation.x")
        drift.fromValue = 0
        drift.toValue = -36
        Self.loop(drift, duration: 70, reverses: true, rate: 10)
        clouds.add(drift, forKey: "drift")

        for (index, group) in grass.enumerated() {
            let sway = CABasicAnimation(keyPath: "transform")
            sway.fromValue = Self.skew(-3)
            sway.toValue = Self.skew(4)
            let (duration, delay) = [(5.5, 0.0), (6.5, 2.0), (4.8, 1.0)][index]
            Self.loop(sway, duration: duration, reverses: true, rate: 30)
            sway.timeOffset = delay
            group.add(sway, forKey: "sway")
        }

        let flight = CABasicAnimation(keyPath: "transform")
        flight.fromValue = CATransform3DIdentity
        flight.toValue = CATransform3DMakeTranslation(-260, -24, 0)
        Self.loop(flight, duration: 26, reverses: false, rate: 20)
        flight.timingFunction = CAMediaTimingFunction(name: .linear)
        bird.add(flight, forKey: "flight")

        let seeds = MeadowSceneSeeds(seed: plan.seed)
        for (layer, fly) in zip(fireflies, seeds.fireflies) {
            let flicker = CAAnimationGroup()
            let opacity = CAKeyframeAnimation(keyPath: "opacity")
            opacity.values = [0, plan.colors.glow, 0.15]
            let wander = CAKeyframeAnimation(keyPath: "transform")
            wander.values = [CATransform3DIdentity, CATransform3DIdentity, CATransform3DMakeTranslation(6, -8, 0)]
            for step in [opacity, wander] {
                step.keyTimes = [0, 0.5, 1]
                step.timingFunctions = [CAMediaTimingFunction(name: .easeInEaseOut),
                                        CAMediaTimingFunction(name: .easeInEaseOut)]
                step.duration = 3.8
            }
            flicker.animations = [opacity, wander]
            Self.loop(flicker, duration: 3.8, reverses: true, rate: 20)
            flicker.timingFunction = CAMediaTimingFunction(name: .linear)
            flicker.timeOffset = -fly.delay
            layer.add(flicker, forKey: "flicker")
        }
    }

    private func still() {
        clouds.removeAllAnimations()
        for layer in grass + fireflies { layer.removeAllAnimations() }
        bird.removeAllAnimations()
    }

    private static func loop(_ animation: CAAnimation, duration: Double, reverses: Bool, rate: Float) {
        animation.duration = duration
        animation.autoreverses = reverses
        animation.repeatCount = .infinity
        animation.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        animation.isRemovedOnCompletion = false
        animation.preferredFrameRateRange = CAFrameRateRange(minimum: rate / 3, maximum: rate, preferred: rate)
    }

    /// CSS's `skewX`, in degrees.
    private static func skew(_ degrees: Double) -> CATransform3D {
        var transform = CATransform3DIdentity
        transform.m21 = tan(degrees * .pi / 180)
        return transform
    }

    // MARK: A picture

    /// The scene at rest, drawn once into an image of `plan.size` at its display scale.
    static func picture(_ plan: MeadowScenePlan) -> CGImage? {
        let width = Int((plan.size.width * plan.displayScale).rounded())
        let height = Int((plan.size.height * plan.displayScale).rounded())
        guard width > 0, height > 0, let context = CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0, space: MeadowArt.space,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        let layer = MeadowSceneLayer()
        layer.apply(plan, moving: false)
        // The scene is laid out y down. A layer's flipped geometry is flipped against its superlayer's, so it is
        // drawn inside one that is y up, as the context is.
        layer.isGeometryFlipped = true
        let holder = CALayer()
        holder.frame = layer.bounds
        layer.frame = holder.bounds
        holder.addSublayer(layer)
        context.scaleBy(x: plan.displayScale, y: plan.displayScale)
        holder.render(in: context)
        return context.makeImage()
    }
}

/// The window's sky behind the panels: the night's stars over its top 60%, and the band of soft clouds that drifts
/// across it once every five minutes. The gradient under them is SwiftUI's.
final class MeadowSkyLayer: CALayer {
    private var colors: MeadowSceneColors?
    private var size = CGSize.zero
    private var displayScale = 0.0
    private var moving = false
    private var drawnAt = 0.0
    private let stars = CALayer(), band = CALayer()
    private var dots: [CALayer] = []
    private var cloudLayers: [CALayer] = []

    override init() {
        super.init()
        stars.masksToBounds = true
        band.anchorPoint = .zero
        addSublayer(stars)
        addSublayer(band)
    }

    override init(layer: Any) { super.init(layer: layer) }
    required init?(coder: NSCoder) { fatalError("not from a coder") }

    func apply(_ colors: MeadowSceneColors, size: CGSize, displayScale: Double, moving: Bool) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        defer { CATransaction.commit() }
        if colors != self.colors { build(colors) }
        if size != self.size || displayScale != self.displayScale || colors != self.colors {
            place(colors, size: size, displayScale: displayScale)
        }
        self.colors = colors
        self.size = size
        self.displayScale = displayScale
        if moving != self.moving {
            if moving {
                // Sliding the band's anchor across half its width moves it one window to the left at any width.
                let drift = CABasicAnimation(keyPath: "anchorPoint.x")
                drift.fromValue = 0
                drift.toValue = 0.5
                drift.duration = 300
                drift.repeatCount = .infinity
                drift.isRemovedOnCompletion = false
                drift.preferredFrameRateRange = CAFrameRateRange(minimum: 5, maximum: 15, preferred: 15)
                band.add(drift, forKey: "drift")
            } else {
                band.removeAllAnimations()
            }
            self.moving = moving
        }
    }

    private func build(_ colors: MeadowSceneColors) {
        stars.sublayers = nil
        dots = colors.stars > 0 ? MeadowSceneSeeds.skyStars.map { star in
            let dot = CALayer()
            dot.backgroundColor = CGColor(gray: 1, alpha: 1)
            dot.opacity = Float(star.opacity)
            stars.addSublayer(dot)
            return dot
        } : []
        stars.opacity = Float(colors.stars)
        band.sublayers = nil
        cloudLayers = (0..<2).flatMap { _ in
            MeadowCloud.sky.map { cloud in
                let layer = CALayer()
                layer.opacity = Float(0.9 * cloud.opacity)
                layer.contentsGravity = .resize
                band.addSublayer(layer)
                return layer
            }
        }
        drawnAt = 0
    }

    private func place(_ colors: MeadowSceneColors, size: CGSize, displayScale: Double) {
        bounds.size = size
        stars.frame = CGRect(x: 0, y: 0, width: size.width, height: size.height * 0.6)
        let crop = MeadowCrop(content: CGSize(width: 1000, height: 400), frame: stars.bounds.size, alignment: .top)
        for (dot, star) in zip(dots, MeadowSceneSeeds.skyStars) {
            let r = star.r * crop.scale
            dot.frame = CGRect(x: crop.origin.x + star.x * crop.scale - r, y: crop.origin.y + star.y * crop.scale - r,
                               width: 2 * r, height: 2 * r)
            dot.cornerRadius = r
            dot.contentsScale = displayScale
        }
        band.bounds = CGRect(x: 0, y: 0, width: size.width * 2, height: 200)
        band.position = .zero
        let (sx, sy) = MeadowSkyLayer.bandScale(size.width)
        let pixels = MeadowArt.quantized(max(sx, sy) * displayScale)
        let redraw = pixels != drawnAt
        drawnAt = pixels
        for (index, layer) in cloudLayers.enumerated() {
            let half = Double(index / MeadowCloud.sky.count), cloud = MeadowCloud.sky[index % MeadowCloud.sky.count]
            let (image, rect) = MeadowArt.skyCloud(index % MeadowCloud.sky.count, colors, pixels: pixels)
            if redraw { layer.contents = image }
            layer.frame = CGRect(x: (1800 * half + cloud.x + rect.minX) * sx, y: (cloud.y + rect.minY) * sy,
                                 width: rect.width * sx, height: rect.height * sy)
        }
    }

    /// Points a unit of the band's 3600 by 240 viewBox, which is stretched over twice the window's width and 200
    /// points of height.
    static func bandScale(_ width: Double) -> (Double, Double) { (2 * width / 3600, 200.0 / 240) }
}

#if os(macOS)
/// An AppKit view that shows a layer of Meadow's scenery and takes no clicks. It is flipped, so AppKit gives its own
/// layer the y-down geometry the scenery is laid out in, whatever the layers above it are.
final class MeadowLayerHost: NSView {
    let root: CALayer

    init(_ root: CALayer) {
        self.root = root
        super.init(frame: .zero)
        wantsLayer = true
        layer?.addSublayer(root)
    }

    required init?(coder: NSCoder) { fatalError("not from a coder") }

    override var isFlipped: Bool { true }

    override func layout() {
        super.layout()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        root.frame = bounds
        CATransaction.commit()
    }

    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override func isAccessibilityElement() -> Bool { false }
}

/// Says whether the view it sits in is in a window, where Core Animation can run the scenery. ImageRenderer has none,
/// and draws an AppKit view as a placeholder, so this one is zero points across and the scenery waits for its word.
struct MeadowLiveProbe: NSViewRepresentable {
    @Binding var live: Bool

    func makeNSView(context: Context) -> Probe {
        let probe = Probe()
        probe.said = { inWindow in
            DispatchQueue.main.async { if live != inWindow { live = inWindow } }
        }
        return probe
    }

    func updateNSView(_ nsView: Probe, context: Context) {}

    final class Probe: NSView {
        var said: ((Bool) -> Void)?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            said?(window != nil)
        }

        override func hitTest(_ point: NSPoint) -> NSView? { nil }
    }
}

/// The scene's layers in a window.
struct MeadowSceneLayers: NSViewRepresentable {
    let plan: MeadowScenePlan
    let moving: Bool

    func makeNSView(context: Context) -> MeadowLayerHost { MeadowLayerHost(MeadowSceneLayer()) }

    func updateNSView(_ nsView: MeadowLayerHost, context: Context) {
        (nsView.root as? MeadowSceneLayer)?.apply(plan, moving: moving)
    }
}

/// The sky's stars and clouds in a window.
struct MeadowSkyLayers: NSViewRepresentable {
    let colors: MeadowSceneColors
    let size: CGSize
    let displayScale: Double
    let moving: Bool

    func makeNSView(context: Context) -> MeadowLayerHost { MeadowLayerHost(MeadowSkyLayer()) }

    func updateNSView(_ nsView: MeadowLayerHost, context: Context) {
        (nsView.root as? MeadowSkyLayer)?.apply(colors, size: size, displayScale: displayScale, moving: moving)
    }
}
#endif
