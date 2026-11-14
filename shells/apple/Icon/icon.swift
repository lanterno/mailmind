// The Mac app's icon, drawn here so it can be changed: a cream envelope whose flap reads as an M, on an afternoon-sky
// tile, with a spark of sunlight at its corner for the help Claude gives. `just apple icon` compiles this and writes
// every size the asset catalog names into Icon/Assets.xcassets/AppIcon.appiconset, with the Contents.json files.
//
// It keeps Apple's macOS grid: the tile is 824 points of a 1024 canvas, with room around it for its shadow. Each size
// is drawn from the shapes, not scaled down from the largest, so the small ones stay sharp.
//
// Only the Mac target has it: compiling an app icon for iOS needs an iOS simulator runtime installed, even for a
// device build, and `just apple build` builds the iOS app on Macs without one.
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// sRGB from a hex number, so the colours below read as a designer writes them.
func colour(_ hex: UInt32, _ alpha: CGFloat = 1) -> CGColor {
    CGColor(
        srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
        blue: CGFloat(hex & 0xFF) / 255, alpha: alpha)
}

let skyTop = colour(0x7CC8FF)
let skyBottom = colour(0x2F6BDF)
let cream = colour(0xFFF8EA)
let creamShade = colour(0xF1E4CC)
let flapInk = colour(0x2C62D3)
let sun = colour(0xFFC53D)
let sunCore = colour(0xFFE7A1)

/// The tile macOS draws its own icons on: a square whose corners are continuous, the curve starting 1.53 radii from
/// each corner and easing into the straight edges with no step in curvature. Each corner is a quarter superellipse.
func tile(_ rect: CGRect) -> CGPath {
    let radius = rect.width * 0.225
    let reach = radius * 1.528665
    // The exponent that puts the corner's middle where a circle of `radius` would be.
    let n = 3.26
    let path = CGMutablePath()
    let steps = 90
    // Each corner's centre, clockwise from the top left in a y-down canvas, and the angle its quarter starts at.
    let corners: [(CGPoint, Double)] = [
        (CGPoint(x: rect.minX + reach, y: rect.minY + reach), Double.pi),
        (CGPoint(x: rect.maxX - reach, y: rect.minY + reach), 3 * Double.pi / 2),
        (CGPoint(x: rect.maxX - reach, y: rect.maxY - reach), 0),
        (CGPoint(x: rect.minX + reach, y: rect.maxY - reach), Double.pi / 2),
    ]
    for (index, (centre, start)) in corners.enumerated() {
        for step in 0...steps {
            let angle = start + Double(step) / Double(steps) * Double.pi / 2
            let c = cos(angle)
            let s = sin(angle)
            let x = pow(abs(c), 2 / n) * (c < 0 ? -1 : 1)
            let y = pow(abs(s), 2 / n) * (s < 0 ? -1 : 1)
            let point = CGPoint(x: centre.x + x * reach, y: centre.y + y * reach)
            if index == 0 && step == 0 { path.move(to: point) } else { path.addLine(to: point) }
        }
    }
    path.closeSubpath()
    return path
}

/// A four-pointed spark: long points up, down, left and right, drawn in toward the centre on curves.
func spark(centre: CGPoint, radius: CGFloat) -> CGPath {
    let path = CGMutablePath()
    let pull = radius * 0.16
    let points = (0..<4).map { index -> CGPoint in
        let angle = CGFloat(index) * .pi / 2 + .pi / 2
        return CGPoint(x: centre.x + cos(angle) * radius, y: centre.y + sin(angle) * radius)
    }
    path.move(to: points[0])
    for index in 0..<4 {
        let next = points[(index + 1) % 4]
        let between = CGFloat(index) * .pi / 2 + .pi / 2 + .pi / 4
        let control = CGPoint(x: centre.x + cos(between) * pull, y: centre.y + sin(between) * pull)
        path.addQuadCurve(to: next, control: control)
    }
    path.closeSubpath()
    return path
}

/// The icon on a canvas of `side` pixels. Coordinates are those of a 1024 canvas with y down, scaled to the side.
func draw(side: Int) -> CGImage {
    let space = CGColorSpace(name: CGColorSpace.sRGB)!
    let context = CGContext(
        data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: 0, space: space,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    let scale = CGFloat(side) / 1024
    context.translateBy(x: 0, y: CGFloat(side))
    context.scaleBy(x: scale, y: -scale)
    context.setShouldAntialias(true)
    context.interpolationQuality = .high

    // The tile: 824 of 1024, inset 100, its shadow under it as Apple's template has it.
    let bounds = CGRect(x: 100, y: 100, width: 824, height: 824)
    let shape = tile(bounds)
    context.saveGState()
    // y is down here, and a shadow's offset is in the device's space, which is y up: negative is down.
    context.setShadow(offset: CGSize(width: 0, height: -10 * scale), blur: 24 * scale, color: colour(0x0B1E4A, 0.32))
    context.addPath(shape)
    context.setFillColor(skyBottom)
    context.fillPath()
    context.restoreGState()
    context.saveGState()
    context.addPath(shape)
    context.clip()
    let sky = CGGradient(colorsSpace: space, colors: [skyTop, skyBottom] as CFArray, locations: [0, 1])!
    context.drawLinearGradient(
        sky, start: CGPoint(x: 512, y: bounds.minY), end: CGPoint(x: 512, y: bounds.maxY), options: [])
    // Light from the top: a soft glow across the upper half.
    let glow = CGGradient(
        colorsSpace: space, colors: [colour(0xFFFFFF, 0.28), colour(0xFFFFFF, 0)] as CFArray, locations: [0, 1])!
    context.drawRadialGradient(
        glow, startCenter: CGPoint(x: 512, y: bounds.minY), startRadius: 0,
        endCenter: CGPoint(x: 512, y: bounds.minY), endRadius: 620, options: [])
    context.restoreGState()

    // The envelope: a cream body, a little shade at its foot, and the flap's V drawn in the sky's ink.
    let body = CGRect(x: 220, y: 380, width: 544, height: 388)
    let envelope = CGPath(roundedRect: body, cornerWidth: 64, cornerHeight: 64, transform: nil)
    context.saveGState()
    context.setShadow(offset: CGSize(width: 0, height: -14 * scale), blur: 30 * scale, color: colour(0x0B1E4A, 0.30))
    context.addPath(envelope)
    context.setFillColor(cream)
    context.fillPath()
    context.restoreGState()
    context.saveGState()
    context.addPath(envelope)
    context.clip()
    let foot = CGGradient(colorsSpace: space, colors: [cream, creamShade] as CFArray, locations: [0, 1])!
    context.drawLinearGradient(
        foot, start: CGPoint(x: 512, y: body.midY), end: CGPoint(x: 512, y: body.maxY), options: [])
    context.restoreGState()

    let flap = CGMutablePath()
    flap.move(to: CGPoint(x: 290, y: 452))
    flap.addLine(to: CGPoint(x: 492, y: 610))
    flap.addLine(to: CGPoint(x: 694, y: 452))
    context.addPath(flap)
    context.setStrokeColor(flapInk)
    context.setLineWidth(52)
    context.setLineCap(.round)
    context.setLineJoin(.round)
    context.strokePath()

    // The spark, ringed in the sky's colour so it stands apart from the envelope it overlaps.
    let centre = CGPoint(x: 774, y: 312)
    context.addPath(spark(centre: centre, radius: 118))
    context.setStrokeColor(skyBottom)
    context.setLineWidth(32)
    context.setLineJoin(.round)
    context.strokePath()
    context.addPath(spark(centre: centre, radius: 118))
    context.setFillColor(sun)
    context.fillPath()
    context.addPath(spark(centre: centre, radius: 50))
    context.setFillColor(sunCore)
    context.fillPath()

    return context.makeImage()!
}

func write(_ image: CGImage, to url: URL) {
    guard let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)
    else { fatalError("can't write \(url.path)") }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else { fatalError("can't write \(url.path)") }
}

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: icon <Assets.xcassets folder>\n".utf8))
    exit(2)
}
let catalog = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let folder = catalog.appending(path: "AppIcon.appiconset")
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)

// The Mac's ten: 16, 32, 128, 256 and 512 points, each at 1x and 2x.
var images: [[String: String]] = []
for points in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let name = "mac-\(points)@\(scale)x.png"
        write(draw(side: points * scale), to: folder.appending(path: name))
        images.append(["filename": name, "idiom": "mac", "scale": "\(scale)x", "size": "\(points)x\(points)"])
    }
}

func describe(_ contents: [String: Any], in folder: URL) throws {
    let json = try JSONSerialization.data(withJSONObject: contents, options: [.prettyPrinted, .sortedKeys])
    try (json + Data("\n".utf8)).write(to: folder.appending(path: "Contents.json"))
}
let info = ["author": "xcode", "version": 1] as [String: Any]
try describe(["images": images, "info": info], in: folder)
try describe(["info": info], in: catalog)
print("drew: \(folder.path)")
