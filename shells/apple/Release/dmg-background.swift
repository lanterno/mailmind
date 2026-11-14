// The picture behind the .dmg's window, drawn here as Icon/icon.swift draws the icon: the afternoon sky, the words
// that say what to do, and an arrow from MailMind to Applications, with a line on the first opening, which macOS stops
// while the app is signed ad hoc. release.sh compiles this, and Release/dmg.py lays the window out over it.
//
//   dmg-background FOLDER
//       Writes FOLDER/background.png (640 by 400) and FOLDER/background@2x.png, which dmgbuild joins for Retina.
//
// Finder draws the icons' names itself, black in light mode and white in dark, on whatever is under them: the band
// they sit on is a blue of luminance 0.18, where both read at about 4.5 to 1.
import CoreGraphics
import CoreText
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// Core Text's weight trait: 0 is regular, 0.3 semibold.
typealias NSFontWeight = CGFloat

func colour(_ hex: UInt32, _ alpha: CGFloat = 1) -> CGColor {
    CGColor(
        srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
        blue: CGFloat(hex & 0xFF) / 255, alpha: alpha)
}

let size = CGSize(width: 640, height: 400)
/// Where Release/dmg.py puts the icons' centres, from the top left, in points.
let app = CGPoint(x: 160, y: 205)
let applications = CGPoint(x: 480, y: 205)

let skyHigh = colour(0x4682E6)
let band = colour(0x3A74D8)
let skyLow = colour(0x2C5FC4)
let cream = colour(0xFFF8EA)
let sun = colour(0xFFC53D)

func text(_ words: String, size: CGFloat, weight: NSFontWeight, colour: CGColor, centre: CGPoint, in context: CGContext) {
    let font = CTFontCreateUIFontForLanguage(.system, size, nil).map {
        CTFontCreateCopyWithAttributes($0, size, nil, CTFontDescriptorCreateWithAttributes(
            [kCTFontTraitsAttribute: [kCTFontWeightTrait: weight]] as CFDictionary))
    }
    let attributes: [NSAttributedString.Key: Any] = [
        NSAttributedString.Key(kCTFontAttributeName as String): font as Any,
        NSAttributedString.Key(kCTForegroundColorAttributeName as String): colour,
    ]
    let line = CTLineCreateWithAttributedString(NSAttributedString(string: words, attributes: attributes))
    let bounds = CTLineGetBoundsWithOptions(line, .useOpticalBounds)
    context.saveGState()
    // The context is flipped to read from the top; Core Text draws upright in an unflipped one.
    context.textMatrix = CGAffineTransform(scaleX: 1, y: -1)
    context.textPosition = CGPoint(x: centre.x - bounds.width / 2 - bounds.minX, y: centre.y + bounds.height / 2 + bounds.minY)
    CTLineDraw(line, context)
    context.restoreGState()
}

func draw(scale: CGFloat) -> CGImage {
    let width = Int(size.width * scale)
    let height = Int(size.height * scale)
    guard
        let context = CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { fatalError("no bitmap context") }
    // Points from the top left, as Finder places icons.
    context.translateBy(x: 0, y: CGFloat(height))
    context.scaleBy(x: scale, y: -scale)

    // The sky: lighter above, the band the icons and their names sit on, deeper below.
    let gradient = CGGradient(
        colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: [skyHigh, band, band, skyLow] as CFArray,
        locations: [0, 0.42, 0.78, 1])!
    context.drawLinearGradient(gradient, start: .zero, end: CGPoint(x: 0, y: size.height), options: [])

    // A lighter sky in the top corner, round the sun the icon has.
    let glow = CGGradient(
        colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: [colour(0xA8DCFF, 0.6), colour(0xA8DCFF, 0)] as CFArray,
        locations: [0, 1])!
    context.drawRadialGradient(
        glow, startCenter: CGPoint(x: 600, y: 22), startRadius: 0, endCenter: CGPoint(x: 600, y: 22), endRadius: 150,
        options: [])
    context.setFillColor(sun)
    context.fillEllipse(in: CGRect(x: 592, y: 14, width: 16, height: 16))

    text("Drag MailMind onto Applications", size: 19, weight: 0.3, colour: cream, centre: CGPoint(x: 320, y: 62), in: context)

    // The arrow, between the two icons (128 points wide), in cream.
    let from = app.x + 64 + 30
    let to = applications.x - 64 - 30
    context.setStrokeColor(colour(0xFFF8EA, 0.9))
    context.setLineWidth(5)
    context.setLineCap(.round)
    context.setLineJoin(.round)
    context.move(to: CGPoint(x: from, y: app.y))
    context.addLine(to: CGPoint(x: to, y: app.y))
    context.move(to: CGPoint(x: to - 16, y: app.y - 16))
    context.addLine(to: CGPoint(x: to, y: app.y))
    context.addLine(to: CGPoint(x: to - 16, y: app.y + 16))
    context.strokePath()

    // High enough that a title bar taller than the window's bounds allow for would cut only sky.
    text(
        "The first time you open it: System Settings › Privacy & Security › Open Anyway", size: 12, weight: 0,
        colour: colour(0xFFFFFF), centre: CGPoint(x: 320, y: 336), in: context)

    guard let image = context.makeImage() else { fatalError("no image") }
    return image
}

func write(_ image: CGImage, to url: URL, scale: CGFloat) {
    guard let file = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
        fatalError("can't write \(url.path)")
    }
    let dpi = 72 * scale
    CGImageDestinationAddImage(file, image, [kCGImagePropertyDPIWidth: dpi, kCGImagePropertyDPIHeight: dpi] as CFDictionary)
    guard CGImageDestinationFinalize(file) else { fatalError("can't write \(url.path)") }
}

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: dmg-background FOLDER\n".utf8))
    exit(1)
}
let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
write(draw(scale: 1), to: folder.appending(path: "background.png"), scale: 1)
write(draw(scale: 2), to: folder.appending(path: "background@2x.png"), scale: 2)
