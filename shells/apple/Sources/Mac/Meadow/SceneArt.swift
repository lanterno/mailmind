import CoreGraphics
import Foundation
import SwiftUI

// The scene's painted parts as bitmaps (#44): the hills, cottage, tree and gate in one, each drifting cloud in its own,
// a firefly's glow and the sky's soft clouds. Each is drawn once for a light and a size and kept; what moves is moved
// by Core Animation, never redrawn.

/// The colours the scenery is painted in, taken from a palette: what a picture is kept for.
struct MeadowSceneColors: Hashable, Sendable {
    let sky1, sky2, sky3, cloud1, cloud2, cloud3, disc, discGlow: Color
    let mountain, hillFar, hillMid, hillNear, hillLit, meadow, leaf1, leaf2, leaf3: Color
    let trunk, roof, wall, window, path, bird: Color
    let stars, glow, sunny: Double

    init(_ p: MeadowPalette) {
        (sky1, sky2, sky3, cloud1, cloud2, cloud3, disc, discGlow) =
            (p.sky1, p.sky2, p.sky3, p.cloud1, p.cloud2, p.cloud3, p.disc, p.discGlow)
        (mountain, hillFar, hillMid, hillNear, hillLit, meadow, leaf1, leaf2, leaf3) =
            (p.mtn, p.hillFar, p.hillMid, p.hillNear, p.hillLit, p.meadow, p.leaf1, p.leaf2, p.leaf3)
        (trunk, roof, wall, window, path, bird) = (p.trunk, p.roof, p.wall, p.window, p.path, p.ink2)
        (stars, glow, sunny) = (p.stars, p.glow, p.sunny)
    }
}

extension Color {
    /// This colour for Core Graphics and Core Animation.
    var meadowCG: CGColor { resolve(in: EnvironmentValues()).cgColor }
}

enum MeadowArt {
    static let space = CGColorSpace(name: CGColorSpace.sRGB)!

    /// The scene's middle: the hills, the cottage or the gate, the tree and the near meadow, with the scene's
    /// texture. It covers the scene from `middleTop` down.
    static let middleTop = 108.0

    /// Pixels a unit, rounded to the nearest twelfth of an octave: a picture is redrawn only when its size changes by
    /// more than about 3%, and shown a little scaled until then.
    static func quantized(_ pixels: Double) -> Double {
        guard pixels > 0 else { return 1 }
        return pow(2, (log2(pixels) * 12).rounded() / 12)
    }

    private struct Key: Hashable {
        let what: String, colors: MeadowSceneColors, pixels: Double
    }

    private final class Box {
        let image: CGImage
        init(_ image: CGImage) { self.image = image }
    }

    /// Kept pictures, given back under memory pressure. A layer showing one keeps its own reference.
    nonisolated(unsafe) private static let kept: NSCache<AnyKeyBox, Box> = {
        let cache = NSCache<AnyKeyBox, Box>()
        cache.totalCostLimit = 12 << 20
        return cache
    }()

    private final class AnyKeyBox: NSObject {
        let key: Key
        init(_ key: Key) { self.key = key }
        override var hash: Int { key.hashValue }
        override func isEqual(_ object: Any?) -> Bool { (object as? AnyKeyBox)?.key == key }
    }

    private static func keep(_ what: String, _ colors: MeadowSceneColors, _ pixels: Double,
                             _ make: () -> CGImage) -> CGImage {
        let key = AnyKeyBox(Key(what: what, colors: colors, pixels: pixels))
        if let box = kept.object(forKey: key) { return box.image }
        let image = make()
        kept.setObject(Box(image), forKey: key, cost: image.bytesPerRow * image.height)
        return image
    }

    // MARK: Drawing

    /// Premultiplied BGRA, the layout Core Animation shows without converting a copy.
    static let bitmapInfo = CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue

    /// A bitmap of `rect` (in scene units) at `pixels` a unit, drawn in scene units with y down.
    static func bitmap(_ rect: CGRect, pixels: Double, _ draw: (CGContext) -> Void) -> CGContext {
        let width = max(1, Int((rect.width * pixels).rounded(.up)))
        let height = max(1, Int((rect.height * pixels).rounded(.up)))
        let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                space: space, bitmapInfo: bitmapInfo)!
        context.translateBy(x: 0, y: Double(height))
        context.scaleBy(x: Double(width) / rect.width, y: -Double(height) / rect.height)
        context.translateBy(x: -rect.minX, y: -rect.minY)
        draw(context)
        return context
    }

    /// Darkens what is drawn by the scene's texture, keeping its alpha: the multiplied `tex` rect, which the mockup
    /// lays over the clouds and the hills alike.
    static func texture(_ context: CGContext) {
        context.saveGState()
        context.resetClip()
        let ctm = context.ctm
        context.concatenate(ctm.inverted())
        context.setBlendMode(.sourceAtop)
        let tile = MeadowNoise.sceneTexture
        context.draw(tile, in: CGRect(x: 0, y: 0, width: tile.width, height: tile.height), byTiling: true)
        context.restoreGState()
    }

    static func linear(_ context: CGContext, _ colors: [Color], _ locations: [CGFloat], top: Double, bottom: Double) {
        let gradient = CGGradient(colorsSpace: space, colors: colors.map(\.meadowCG) as CFArray, locations: locations)!
        context.drawLinearGradient(gradient, start: CGPoint(x: 0, y: top), end: CGPoint(x: 0, y: bottom),
                                   options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
    }

    static func fill(_ context: CGContext, _ path: CGPath, _ color: Color, alpha: Double = 1) {
        context.saveGState()
        context.setAlpha(alpha)
        context.addPath(path)
        context.setFillColor(color.meadowCG)
        context.fillPath()
        context.restoreGState()
    }

    static func stroke(_ context: CGContext, _ path: CGPath, _ color: Color, width: Double, alpha: Double = 1) {
        context.saveGState()
        context.setAlpha(alpha)
        context.addPath(path)
        context.setStrokeColor(color.meadowCG)
        context.setLineWidth(width)
        context.setLineCap(.round)
        context.setLineJoin(.round)
        context.strokePath()
        context.restoreGState()
    }

    // MARK: The middle

    /// The hills, the cottage or the gate, the tree and the near meadow, for `rect` of the scene.
    static func middle(_ colors: MeadowSceneColors, gate: Bool, pixels: Double) -> CGImage {
        keep(gate ? "middle-gate" : "middle", colors, pixels) {
            let rect = CGRect(x: -4, y: middleTop, width: 1208, height: 320 - middleTop)
            return bitmap(rect, pixels: pixels) { context in
                drawMiddle(context, colors, gate: gate)
                texture(context)
            }.makeImage()!
        }
    }

    static func drawMiddle(_ context: CGContext, _ c: MeadowSceneColors, gate: Bool) {
        let painted = MeadowPaint.shared
        fill(context, painted.mountain, c.mountain, alpha: 0.85)
        fill(context, painted.hillFar, c.hillFar)
        context.saveGState()
        context.addPath(painted.hillMid)
        context.clip()
        let mid = meadowPath(MeadowShapes.hillMid).boundingBoxOfPath
        linear(context, [c.hillMid, c.hillNear], [0, 1], top: mid.minY, bottom: mid.maxY)
        context.restoreGState()
        if !gate { drawHouse(context, c) }
        drawTree(context, c)
        if gate { drawGate(context, c) }
        context.saveGState()
        let nearPath = meadowPath(MeadowShapes.hillNear)
        context.addPath(nearPath)
        context.clip()
        let near = nearPath.boundingBoxOfPath
        linear(context, [c.meadow, c.hillNear], [0, 1], top: near.minY, bottom: near.maxY)
        context.restoreGState()
    }

    private static func drawHouse(_ context: CGContext, _ c: MeadowSceneColors) {
        context.saveGState()
        context.translateBy(x: MeadowShapes.house.x, y: MeadowShapes.house.y)
        fill(context, CGPath(ellipseIn: CGRect(x: 2 - 26, y: 1 - 3, width: 52, height: 6), transform: nil), c.leaf3,
             alpha: 0.25)
        fill(context, CGPath(rect: CGRect(x: -14, y: -15, width: 30, height: 15), transform: nil), c.wall)
        fill(context, CGPath(rect: CGRect(x: 6, y: -27, width: 4, height: 9), transform: nil), c.trunk)
        fill(context, meadowPath(MeadowShapes.roof), c.roof)
        fill(context, CGPath(rect: CGRect(x: -8, y: -11, width: 6, height: 6), transform: nil), c.window)
        if c.glow > 0 {
            // The lit window's glow: #FFC768 from .7 to nothing over its radius of 16.
            context.saveGState()
            context.setAlpha(c.glow)
            context.addEllipse(in: CGRect(x: -5 - 16, y: -8 - 16, width: 32, height: 32))
            context.clip()
            let warm = [CGColor(srgbRed: 1, green: 0xC7 / 255, blue: 0x68 / 255, alpha: 0.7),
                        CGColor(srgbRed: 1, green: 0xC7 / 255, blue: 0x68 / 255, alpha: 0)]
            let gradient = CGGradient(colorsSpace: space, colors: warm as CFArray, locations: [0, 1])!
            context.drawRadialGradient(gradient, startCenter: CGPoint(x: -5, y: -8), startRadius: 0,
                                       endCenter: CGPoint(x: -5, y: -8), endRadius: 16, options: [])
            context.restoreGState()
        }
        context.restoreGState()
        stroke(context, meadowPath(MeadowShapes.lane), c.path, width: 3.2, alpha: 0.55)
    }

    private static func drawTree(_ context: CGContext, _ c: MeadowSceneColors) {
        context.saveGState()
        context.translateBy(x: MeadowShapes.tree.x, y: MeadowShapes.tree.y)
        fill(context, CGPath(ellipseIn: CGRect(x: 6 - 52, y: 2 - 5, width: 104, height: 10), transform: nil), c.leaf3,
             alpha: 0.3)
        fill(context, meadowPath(MeadowShapes.trunk), c.trunk)
        stroke(context, meadowPath(MeadowShapes.branches), c.trunk, width: 3)
        context.translateBy(x: MeadowShapes.canopyOffset.x, y: MeadowShapes.canopyOffset.y)
        let gradient = CGGradient(colorsSpace: space, colors: [c.leaf1, c.leaf2, c.leaf3].map(\.meadowCG) as CFArray,
                                  locations: [0, 0.55, 1])!
        for (index, (x, y, r)) in MeadowShapes.canopy.enumerated() {
            context.saveGState()
            context.addPath(MeadowPaint.shared.canopy[index])
            context.clip()
            // The leaf gradient's bounding box is the circle's: centred at (.34, .28) of it, reaching .85 of its side.
            let centre = CGPoint(x: x - r + 0.68 * r, y: y - r + 0.56 * r)
            context.drawRadialGradient(gradient, startCenter: centre, startRadius: 0, endCenter: centre,
                                       endRadius: 1.7 * r, options: [.drawsAfterEndLocation])
            context.restoreGState()
        }
        context.restoreGState()
    }

    private static func drawGate(_ context: CGContext, _ c: MeadowSceneColors) {
        context.saveGState()
        context.translateBy(x: MeadowShapes.gate.x, y: MeadowShapes.gate.y)
        stroke(context, meadowPath(MeadowShapes.gateRail), c.trunk, width: 2, alpha: 0.55)
        stroke(context, meadowPath(MeadowShapes.fencePosts), c.trunk, width: 3)
        stroke(context, meadowPath(MeadowShapes.fenceRails), c.trunk, width: 3)
        for x in [-34.0, 26] {
            fill(context, CGPath(roundedRect: CGRect(x: x, y: -46, width: 8, height: 66), cornerWidth: 2,
                                 cornerHeight: 2, transform: nil), c.trunk)
        }
        stroke(context, meadowPath(MeadowShapes.gateArch), c.roof, width: 7)
        stroke(context, meadowPath(MeadowShapes.gateBars), c.wall, width: 3.2)
        stroke(context, meadowPath(MeadowShapes.gateStiles), c.wall, width: 2.4)
        context.restoreGState()
    }

    // MARK: Clouds

    /// One of the scene's drifting clouds, painted, and where it sits in the scene.
    static func sceneCloud(_ index: Int, _ colors: MeadowSceneColors, pixels: Double) -> (CGImage, CGRect) {
        let rect = MeadowPaint.shared.cloudBounds[index]
        let image = keep("cloud\(index)", colors, pixels) {
            bitmap(rect, pixels: pixels) { context in
                for (puff, outline) in zip(MeadowCloud.puffs, MeadowPaint.shared.clouds[index]) {
                    let cloud = MeadowCloud.scene[index]
                    puffFill(context, outline, top: cloud.y + cloud.scale * (puff.1 - puff.2),
                             bottom: cloud.y + cloud.scale * (puff.1 + puff.2), colors, middle: 0.5)
                }
                texture(context)
            }.makeImage()!
        }
        return (image, rect)
    }

    private static func puffFill(_ context: CGContext, _ outline: CGPath, top: Double, bottom: Double,
                                 _ c: MeadowSceneColors, middle: CGFloat) {
        context.saveGState()
        context.addPath(outline)
        context.clip()
        linear(context, [c.cloud1, c.cloud2, c.cloud3], [0, middle, 1], top: top, bottom: bottom)
        context.restoreGState()
    }

    /// One of the sky's clouds, blurred as the band's are (2.5 of its units), in units about its middle, and the rect
    /// it covers there.
    static func skyCloud(_ index: Int, _ colors: MeadowSceneColors, pixels: Double) -> (CGImage, CGRect) {
        let cloud = MeadowCloud.sky[index]
        let s = cloud.scale
        let rect = CGRect(x: -106 * s - 8, y: -126 * s - 8, width: 212 * s + 16, height: 150 * s + 16)
        let image = keep("sky\(index)", colors, pixels) {
            let context = bitmap(rect, pixels: pixels) { context in
                for (dx, dy, r) in MeadowCloud.puffs {
                    let circle = CGRect(x: s * (dx - r), y: s * (dy - r), width: 2 * s * r, height: 2 * s * r)
                    let outline = CGMutablePath()
                    outline.addEllipse(in: circle)
                    context.saveGState()
                    context.clip(to: CGRect(x: -1000, y: -1000, width: 2000, height: 1000 + s * MeadowCloud.floor))
                    puffFill(context, outline, top: circle.minY, bottom: circle.maxY, colors, middle: 0.6)
                    context.restoreGState()
                }
            }
            meadowBlur(context, sigma: 2.5 * pixels)
            return context.makeImage()!
        }
        return (image, rect)
    }

    /// A firefly: a dot of #FFE59A, 1.8 across its radius, blurred 2.2. Its rect is about its middle.
    static func firefly(pixels: Double) -> (CGImage, CGRect) {
        let rect = CGRect(x: -9, y: -9, width: 18, height: 18)
        let warm = MeadowSceneColors(MeadowPalette.night)   // only for the key: the glow is the same in every light
        let image = keep("firefly", warm, pixels) {
            let context = bitmap(rect, pixels: pixels) { context in
                context.setFillColor(CGColor(srgbRed: 1, green: 0xE5 / 255, blue: 0x9A / 255, alpha: 1))
                context.fillEllipse(in: CGRect(x: -1.8, y: -1.8, width: 3.6, height: 3.6))
            }
            meadowBlur(context, sigma: 2.2 * pixels)
            return context.makeImage()!
        }
        return (image, rect)
    }
}

/// The paint filter's wobble: feTurbulence at (.018, .04), two octaves, seed 4, moving every edge of the clouds, the
/// mountains, the far and middle hills and the tree's canopy by up to 3.5 units, as its displacement map does. The
/// edges are moved here, once, rather than every pixel each time the scene is drawn.
final class MeadowPaint: @unchecked Sendable {   // immutable once made
    static let shared = MeadowPaint()

    let mountain: CGPath, hillFar: CGPath, hillMid: CGPath
    /// Each scene cloud's puffs, each cut flat at the cloud's floor.
    let clouds: [[CGPath]]
    let cloudBounds: [CGRect]
    /// The canopy's circles, in the canopy's own units (about its middle).
    let canopy: [CGPath]

    private init() {
        let field = MeadowTurbulence(seed: 4)
        // The map shows at each point what lies `7 * (noise - .5)` away, so an edge at `p` shows where `q + d(q) = p`.
        func moved(_ p: CGPoint, origin: CGPoint) -> CGPoint {
            var q = p
            for _ in 0..<3 {
                let x = q.x + origin.x, y = q.y + origin.y
                let dx = 7 * (field.value(0, x: x, y: y, frequency: (0.018, 0.04), octaves: 2) - 0.5)
                let dy = 7 * (field.value(1, x: x, y: y, frequency: (0.018, 0.04), octaves: 2) - 0.5)
                q = CGPoint(x: p.x - dx, y: p.y - dy)
            }
            return q
        }
        func wobbled(_ path: CGPath, origin: CGPoint = .zero) -> CGPath {
            let result = CGMutablePath()
            for polygon in meadowFlatten(path, step: 1.5) {
                let points = polygon.map { moved($0, origin: origin) }
                result.addLines(between: points)
                result.closeSubpath()
            }
            return result
        }
        mountain = wobbled(meadowPath(MeadowShapes.mountain))
        hillFar = wobbled(meadowPath(MeadowShapes.hillFar))
        hillMid = wobbled(meadowPath(MeadowShapes.hillMid))
        var clouds: [[CGPath]] = []
        var bounds: [CGRect] = []
        for cloud in MeadowCloud.scene {
            let floor = cloud.y + cloud.scale * MeadowCloud.floor
            let puffs = MeadowCloud.puffs.map { dx, dy, r in
                let centre = CGPoint(x: cloud.x + cloud.scale * dx, y: cloud.y + cloud.scale * dy)
                let radius = cloud.scale * r
                let steps = max(24, Int((2 * .pi * radius / 1.5).rounded(.up)))
                let points = (0..<steps).map { i -> CGPoint in
                    let angle = 2 * Double.pi * Double(i) / Double(steps)
                    return CGPoint(x: centre.x + radius * cos(angle), y: min(floor, centre.y + radius * sin(angle)))
                }
                let path = CGMutablePath()
                path.addLines(between: points.map { moved($0, origin: .zero) })
                path.closeSubpath()
                return path as CGPath
            }
            clouds.append(puffs)
            let box = puffs.reduce(CGRect.null) { $0.union($1.boundingBoxOfPath) }
            bounds.append(box.insetBy(dx: -1, dy: -1).integral)
        }
        self.clouds = clouds
        cloudBounds = bounds
        canopy = MeadowShapes.canopy.map { x, y, r in
            // The canopy's filter works in the canopy's own units, so its noise is read there.
            wobbled(CGPath(ellipseIn: CGRect(x: x - r, y: y - r, width: 2 * r, height: 2 * r), transform: nil))
        }
    }
}

/// A path as polygons, its curves cut into pieces at most `step` long, its straight edges too, so every part of an edge
/// can be moved.
func meadowFlatten(_ path: CGPath, step: Double) -> [[CGPoint]] {
    var polygons: [[CGPoint]] = []
    var current: [CGPoint] = []
    var last = CGPoint.zero
    func line(to end: CGPoint) {
        let n = max(1, Int((hypot(end.x - last.x, end.y - last.y) / step).rounded(.up)))
        for i in 1...n {
            let t = Double(i) / Double(n)
            current.append(CGPoint(x: last.x + (end.x - last.x) * t, y: last.y + (end.y - last.y) * t))
        }
        last = end
    }
    func curve(_ points: [CGPoint]) {
        var length = 0.0
        for (a, b) in zip([last] + points, points) { length += hypot(b.x - a.x, b.y - a.y) }
        let n = max(1, Int((length / step).rounded(.up)))
        let p0 = last
        for i in 1...n {
            let t = Double(i) / Double(n), u = 1 - t
            let point: CGPoint
            if points.count == 2 {
                point = CGPoint(x: u * u * p0.x + 2 * u * t * points[0].x + t * t * points[1].x,
                                y: u * u * p0.y + 2 * u * t * points[0].y + t * t * points[1].y)
            } else {
                let a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t
                point = CGPoint(x: a * p0.x + b * points[0].x + c * points[1].x + d * points[2].x,
                                y: a * p0.y + b * points[0].y + c * points[1].y + d * points[2].y)
            }
            current.append(point)
        }
        last = points[points.count - 1]
    }
    var start = CGPoint.zero
    path.applyWithBlock { element in
        let e = element.pointee
        switch e.type {
        case .moveToPoint:
            if current.count > 1 { polygons.append(current) }
            last = e.points[0]
            start = last
            current = [last]
        case .addLineToPoint:
            line(to: e.points[0])
        case .addQuadCurveToPoint:
            curve([e.points[0], e.points[1]])
        case .addCurveToPoint:
            curve([e.points[0], e.points[1], e.points[2]])
        case .closeSubpath:
            line(to: start)
        @unknown default:
            break
        }
    }
    if current.count > 1 { polygons.append(current) }
    return polygons
}
