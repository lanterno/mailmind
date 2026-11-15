import AppKit
import SwiftUI
import XCTest

/// Meadow's icons are the mockup's SVG, read by SVGPath: every command, absolute and relative, a command's numbers
/// repeated without it, arcs as curves, and every icon read to a path inside its grid.
final class MeadowIconTests: XCTestCase {
    /// A path's elements as text, to three places, so two paths compare by what they draw.
    private func elements(_ path: Path) -> [String] {
        func p(_ point: CGPoint) -> String {
            String(format: "%.3f,%.3f", point.x, point.y).replacingOccurrences(of: "-0.000", with: "0.000")
        }
        var said: [String] = []
        path.forEach { element in
            switch element {
            case let .move(to): said.append("M\(p(to))")
            case let .line(to): said.append("L\(p(to))")
            case let .quadCurve(to, control): said.append("Q\(p(control)) \(p(to))")
            case let .curve(to, control1, control2): said.append("C\(p(control1)) \(p(control2)) \(p(to))")
            case .closeSubpath: said.append("Z")
            }
        }
        return said
    }

    private func read(_ data: String) throws -> [String] { elements(try SVGPath.parse(data)) }

    // MARK: Commands

    func testMovesAndLinesAbsoluteAndRelative() throws {
        XCTAssertEqual(try read("M1 2L3 4l1 1"), ["M1.000,2.000", "L3.000,4.000", "L4.000,5.000"])
        XCTAssertEqual(try read("m1 2l2 2L0 0"), ["M1.000,2.000", "L3.000,4.000", "L0.000,0.000"])
        // A relative moveto after the first moves from where the path is.
        XCTAssertEqual(try read("M1 1L2 2m1 1l1 0"), ["M1.000,1.000", "L2.000,2.000", "M3.000,3.000", "L4.000,3.000"])
    }

    func testHorizontalAndVerticalLines() throws {
        XCTAssertEqual(try read("M1 1H5V6h-2v-3"),
                       ["M1.000,1.000", "L5.000,1.000", "L5.000,6.000", "L3.000,6.000", "L3.000,3.000"])
    }

    func testCubicsAndTheirSmoothForm() throws {
        let absolute = try read("M0 0C1 2 3 4 5 6S9 10 11 12")
        let relative = try read("m0 0c1 2 3 4 5 6s4 4 6 6")
        // S's first control point is the last curve's second, reflected about where the curve ended.
        XCTAssertEqual(absolute, ["M0.000,0.000", "C1.000,2.000 3.000,4.000 5.000,6.000",
                                  "C7.000,8.000 9.000,10.000 11.000,12.000"])
        XCTAssertEqual(relative, absolute)
    }

    func testASmoothCurveAfterAnotherKindStartsAtTheCurrentPoint() throws {
        XCTAssertEqual(try read("M0 0L1 1S3 3 4 4"),
                       ["M0.000,0.000", "L1.000,1.000", "C1.000,1.000 3.000,3.000 4.000,4.000"])
        XCTAssertEqual(try read("M0 0Q1 1 2 0S3 3 4 4").last, "C2.000,0.000 3.000,3.000 4.000,4.000")
    }

    func testQuadraticsAndTheirSmoothForm() throws {
        let absolute = try read("M0 0Q1 1 2 0T4 0")
        XCTAssertEqual(absolute, ["M0.000,0.000", "Q1.000,1.000 2.000,0.000", "Q3.000,-1.000 4.000,0.000"])
        XCTAssertEqual(try read("m0 0q1 1 2 0t2 0"), absolute)
        // T after T reflects the control point T made.
        XCTAssertEqual(try read("M0 0Q1 1 2 0T4 0T6 0").last, "Q5.000,1.000 6.000,0.000")
    }

    func testCloseGoesBackToTheSubpathsStart() throws {
        XCTAssertEqual(try read("M1 1L5 1L5 5Zl1 1"),
                       ["M1.000,1.000", "L5.000,1.000", "L5.000,5.000", "Z", "M1.000,1.000", "L2.000,2.000"])
        XCTAssertEqual(try read("M1 1h4v4zM9 9h1"),
                       ["M1.000,1.000", "L5.000,1.000", "L5.000,5.000", "Z", "M9.000,9.000", "L10.000,9.000"])
    }

    // MARK: Numbers

    func testACommandsNumbersRepeatWithoutIt() throws {
        // After a moveto they are linetos, relative after a relative one.
        XCTAssertEqual(try read("M0 0 1 1 2 2"), ["M0.000,0.000", "L1.000,1.000", "L2.000,2.000"])
        XCTAssertEqual(try read("m1 1 1 1"), ["M1.000,1.000", "L2.000,2.000"])
        XCTAssertEqual(try read("M0 0l1 1 1 1h1 1"),
                       ["M0.000,0.000", "L1.000,1.000", "L2.000,2.000", "L3.000,2.000", "L4.000,2.000"])
        XCTAssertEqual(try read("M0 0c1 0 1 0 1 1 0 1 0 1 1 1").count, 3)
        // Two half circles, each two quarter-turn curves, the second from where the first ended.
        let arcs = try read("M0 0a1 1 0 0 1 2 0 1 1 0 0 1 2 0")
        XCTAssertEqual(arcs.count, 5)
        XCTAssertEqual(arcs.last?.hasSuffix(" 4.000,0.000"), true)
    }

    func testNumbersRunTogetherAsSVGWritesThem() throws {
        // A sign or a second point starts the next number; commas count as space; exponents are read.
        XCTAssertEqual(try read("M.5.5-1-1"), ["M0.500,0.500", "L-1.000,-1.000"])
        XCTAssertEqual(try read("M1,2,3,4"), ["M1.000,2.000", "L3.000,4.000"])
        XCTAssertEqual(try read("M1e1 2E-1l+1 -.5"), ["M10.000,0.200", "L11.000,-0.300"])
        XCTAssertEqual(try read("  M 1 2 \n L 3 4  "), ["M1.000,2.000", "L3.000,4.000"])
    }

    func testDataSVGCantReadIsRefused() {
        let unreadable = ["L1 1", "1 1", "M1", "M1 x", "M1 1Q", "M0 0A1 1 0 2 0 3 3", "M0 0 L", "M0 0K1 1", "M- 1",
                          "M1 1 2"]
        for bad in unreadable {
            XCTAssertThrowsError(try SVGPath.parse(bad), bad)
        }
        XCTAssertEqual(try read(""), [])
    }

    // MARK: Arcs

    /// The points an arc's curves pass through, sampled, so each can be checked against the circle.
    private func samples(_ path: Path) -> [CGPoint] {
        var points: [CGPoint] = []
        var current = CGPoint.zero
        path.forEach { element in
            switch element {
            case let .move(to), let .line(to):
                current = to
                points.append(to)
            case let .curve(to, c1, c2):
                for step in 1...8 {
                    let t = CGFloat(step) / 8
                    let u = 1 - t
                    let a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t
                    points.append(CGPoint(x: a * current.x + b * c1.x + c * c2.x + d * to.x,
                                          y: a * current.y + b * c1.y + c * c2.y + d * to.y))
                }
                current = to
            default: break
            }
        }
        return points
    }

    private func assertOnCircle(_ path: Path, centre: CGPoint, radius: CGFloat, file: StaticString = #filePath,
                                line: UInt = #line) {
        for point in samples(path) {
            XCTAssertEqual(hypot(point.x - centre.x, point.y - centre.y), radius, accuracy: radius * 0.001,
                           "\(point)", file: file, line: line)
        }
    }

    func testAHalfCircleArcGoesTheWayItsSweepSays() throws {
        // SVG's y runs down, so a positive sweep from left to right passes above.
        let over = try SVGPath.parse("M0 0A5 5 0 0 1 10 0")
        let under = try SVGPath.parse("M0 0A5 5 0 0 0 10 0")
        assertOnCircle(over, centre: CGPoint(x: 5, y: 0), radius: 5)
        assertOnCircle(under, centre: CGPoint(x: 5, y: 0), radius: 5)
        XCTAssertEqual(over.boundingRect.minY, -5, accuracy: 0.01)
        XCTAssertEqual(under.boundingRect.maxY, 5, accuracy: 0.01)
        XCTAssertEqual(elements(over).last?.hasSuffix(" 10.000,0.000"), true, "it ends where the data says")
        XCTAssertEqual(elements(try SVGPath.parse("m0 0a5 5 0 0 1 10 0")), elements(over))
    }

    func testTheLargeArcFlagPicksTheLongWayRound() throws {
        // From the top of a circle of radius 5 about (5, 5) to its right: a quarter, or three quarters.
        let small = try SVGPath.parse("M5 0A5 5 0 0 1 10 5")
        let large = try SVGPath.parse("M5 0A5 5 0 1 0 10 5")
        assertOnCircle(small, centre: CGPoint(x: 5, y: 5), radius: 5)
        assertOnCircle(large, centre: CGPoint(x: 5, y: 5), radius: 5)
        XCTAssertEqual(elements(small).count, 2, "a quarter turn is one curve")
        XCTAssertEqual(elements(large).count, 4, "three quarters are three")
        XCTAssertEqual(large.boundingRect.width, 10, accuracy: 0.01)
        XCTAssertEqual(large.boundingRect.height, 10, accuracy: 0.01)
    }

    func testArcFlagsMayRunIntoTheNumbersAfterThem() throws {
        XCTAssertEqual(elements(try SVGPath.parse("M0 0a5 5 0 0110 0")),
                       elements(try SVGPath.parse("M0 0a5 5 0 0 1 10 0")))
    }

    func testRadiiTooSmallToReachAreScaledUp() throws {
        let path = try SVGPath.parse("M0 0A1 1 0 0 1 10 0")
        assertOnCircle(path, centre: CGPoint(x: 5, y: 0), radius: 5)
    }

    func testAnArcWithNoRadiusIsALineAndOneToItsStartIsNothing() throws {
        XCTAssertEqual(try read("M0 0A0 5 0 0 1 10 0"), ["M0.000,0.000", "L10.000,0.000"])
        XCTAssertEqual(try read("M3 3A5 5 0 0 1 3 3"), ["M3.000,3.000"])
    }

    func testARotatedEllipse() throws {
        // An ellipse 10 by 5 turned upright: from its top to its bottom, round the right, it is 5 wide, 20 high.
        let path = try SVGPath.parse("M0 0A10 5 90 0 1 0 20")
        XCTAssertEqual(path.boundingRect.maxX, 5, accuracy: 0.01)
        XCTAssertEqual(path.boundingRect.minX, 0, accuracy: 0.01)
        XCTAssertEqual(path.boundingRect.height, 20, accuracy: 0.01)
    }

    // MARK: Shapes and icons

    func testCirclesAndRoundedRectanglesAreTheirShapes() throws {
        let circle = try SVGPath.path([.circle(12, 12, 4)])
        XCTAssertEqual(circle.boundingRect, CGRect(x: 8, y: 8, width: 8, height: 8))
        let rect = try SVGPath.path([.rect(3, 4.5, 18, 4.5, 1.2)])
        XCTAssertEqual(rect.boundingRect, CGRect(x: 3, y: 4.5, width: 18, height: 4.5))
        XCTAssertFalse(rect.contains(CGPoint(x: 3.05, y: 4.55)), "its corners are rounded")
    }

    func testEveryIconReadsToAPathInsideItsGrid() throws {
        for icon in MeadowIcon.allCases {
            let path = try SVGPath.path(icon.parts)
            XCTAssertFalse(path.isEmpty, "\(icon)")
            XCTAssertEqual(icon.path, path, "\(icon) is drawn from its parts")
            let bounds = path.boundingRect
            XCTAssertTrue(CGRect(x: 0, y: 0, width: 24, height: 24).insetBy(dx: -0.5, dy: -0.5).contains(bounds),
                          "\(icon) stays on its grid: \(bounds)")
            XCTAssertGreaterThan(max(bounds.width, bounds.height), 10, "\(icon) is drawn at the grid's scale")
        }
    }

    func testIconsKeepTheMockupsNames() {
        XCTAssertEqual(MeadowIcon(rawValue: "replyall"), .replyAll)
        XCTAssertEqual(MeadowIcon(rawValue: "cloud-off"), .cloudOff)
        XCTAssertEqual(MeadowIcon(rawValue: "bell-off"), .bellOff)
        XCTAssertEqual(Set(MeadowIcon.allCases.map(\.rawValue)).count, MeadowIcon.allCases.count)
    }

    func testAnIconScalesToItsSize() {
        let path = MeadowIcon.plus.path(size: 18)
        XCTAssertEqual(path.boundingRect, CGRect(x: 3.75, y: 3.75, width: 10.5, height: 10.5))
        let shape = MeadowIconShape(.plus).path(in: CGRect(x: 10, y: 0, width: 30, height: 18))
        XCTAssertEqual(shape.boundingRect.midX, 25, accuracy: 0.001, "centred in a wider rect")
        XCTAssertEqual(MeadowIcon.strokeStyle(size: 18).lineWidth, 1.3125, accuracy: 0.0001)
        XCTAssertEqual(MeadowIcon.strokeStyle(size: 14, stroke: 2.6).lineWidth, 2.6 * 14 / 24, accuracy: 0.0001)
    }

    func testEveryStandInIsASymbolTheSystemHas() {
        for icon in MeadowIcon.allCases {
            let symbol = NSImage(systemSymbolName: icon.stand, accessibilityDescription: nil)
            XCTAssertNotNil(symbol, "\(icon): \(icon.stand)")
        }
    }

    // MARK: AppKit

    /// The alpha of every pixel an image draws at twice its size, as a Retina screen would.
    private func alpha(_ image: NSImage) throws -> [UInt8] {
        let side = Int(image.size.width * 2)
        let rep = try XCTUnwrap(NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: side, pixelsHigh: side,
                                                 bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                                 colorSpaceName: .deviceRGB, bytesPerRow: side * 4, bitsPerPixel: 32))
        rep.size = image.size
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        image.draw(in: CGRect(origin: .zero, size: image.size))
        NSGraphicsContext.restoreGraphicsState()
        let bytes = try XCTUnwrap(rep.bitmapData)
        return (0..<(side * side)).map { bytes[$0 * 4 + 3] }
    }

    func testAnImageForAppKitDrawsTheIconInItsColour() throws {
        let image = MeadowIcon.minus.image(size: 18, color: .red)
        XCTAssertEqual(image.size, NSSize(width: 18, height: 18))
        XCTAssertFalse(image.isTemplate)
        let drawn = try alpha(image)
        // The minus runs across the middle and nowhere else: the top row stays clear, the middle row is drawn.
        XCTAssertEqual(drawn[0..<36].max(), 0)
        XCTAssertGreaterThan(drawn[18 * 36 + 18], 200)
        // The right way up, as SVG's y runs down: the down chevron's point is at (12, 15), and (12, 9) is clear.
        let down = try alpha(MeadowIcon.down.image(size: 24, color: .black))
        XCTAssertGreaterThan(down[30 * 48 + 24], 200)
        XCTAssertEqual(down[18 * 48 + 24], 0)
    }

    func testATemplateForMenus() {
        let image = MeadowIcon.archive.template(size: 16)
        XCTAssertTrue(image.isTemplate)
        XCTAssertEqual(image.size, NSSize(width: 16, height: 16))
    }
}
