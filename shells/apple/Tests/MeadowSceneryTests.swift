import AppKit
import QuartzCore
import SwiftUI
import XCTest

/// Meadow's sky, scene and grain draw what the mockup's `scene()`, `skyClouds()` and `--grain` draw: the same seeded
/// numbers, each caller's crop, the scene right side up, and no motion under Reduce Motion. With
/// `TEST_RUNNER_SCREENS=1` given to `xcodebuild test`, each light's sky, scenes and grain go to build/screens/.
@MainActor
final class MeadowSceneryTests: XCTestCase {
    private static let folder = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        .appendingPathComponent("../build/screens").standardized
    private static var writing: Bool { ProcessInfo.processInfo.environment["SCREENS"] != nil }

    // MARK: The seeded numbers, against what the mockup's own code gives

    func testTheGeneratorDrawsTheMockupsSequence() {
        // `rnd(4)`'s first eight, as the mockup's JavaScript computes them.
        let expected = [3.1305477037702444e-05, 0.526151152572665, 0.022421288780132907, 0.8346005276937971,
                        0.13106894964867688, 0.8758367453123614, 0.1881784648579445, 0.7154588674732758]
        var random = MeadowRandom(seed: 4)
        XCTAssertEqual(expected.map { _ in random.next() }, expected)
    }

    func testNumbersRoundAsJavaScriptWritesThem() {
        XCTAssertEqual(meadowFixed(2.5, 0), 3)
        XCTAssertEqual(meadowFixed(-2.5, 0), -3)
        XCTAssertEqual(meadowFixed(0.125, 2), 0.13)
        // 1.005 is a little under itself as a double, so `toFixed(2)` writes 1.00.
        XCTAssertEqual(meadowFixed(1.005, 2), 1)
        XCTAssertEqual(meadowFixed(316.84, 1), 316.8)
    }

    /// The scene's stars, grass and fireflies for a seed, summed as the mockup's markup writes them (a lit blade
    /// counts one): the sums are what `scene()` gave in Chromium.
    private func sums(_ seeds: MeadowSceneSeeds) -> (stars: Double, blades: Double, fireflies: Double) {
        let stars = seeds.stars.reduce(0) { $0 + $1.x + $1.y + $1.r + $1.opacity }
        let blades = seeds.grass.joined().reduce(0) {
            $0 + $1.x + $1.base + $1.cx + $1.cy + $1.tx + $1.ty + ($1.lit ? 1 : 0) + $1.width
        }
        let fireflies = seeds.fireflies.reduce(0) { $0 + $1.delay + $1.x + $1.y }
        return (stars, blades, fireflies)
    }

    func testASeedDrawsTheMockupsStarsGrassAndFireflies() {
        let seeds = MeadowSceneSeeds(seed: 21)
        XCTAssertEqual(seeds.stars.count, 70)
        XCTAssertEqual(seeds.grass.map(\.count), [46, 46, 46])
        XCTAssertEqual(seeds.fireflies.count, 12)
        XCTAssertEqual(seeds.stars.first, MeadowStar(x: 0, y: 130, r: 1.43, opacity: 0.68))
        XCTAssertEqual(seeds.stars.last, MeadowStar(x: 377, y: 78, r: 1.43, opacity: 0.86))
        XCTAssertEqual(seeds.grass.first?.first, MeadowBlade(x: 88, base: 316.8, cx: -1.5, cy: -8.4, tx: -3.7,
                                                             ty: -15.2, lit: false, width: 1.4))
        XCTAssertEqual(seeds.grass.last?.last, MeadowBlade(x: 996.5, base: 317.4, cx: 0.6, cy: -16.7, tx: 1.6,
                                                           ty: -30.4, lit: false, width: 1.7))
        XCTAssertEqual(seeds.fireflies.first, MeadowFirefly(x: 1027, y: 284, delay: -3.9))
        XCTAssertEqual(seeds.fireflies.last, MeadowFirefly(x: 723, y: 252, delay: -3))

        let mockup: [Int: (Double, Double, Double)] = [
            0: (52971.76, 121494.1, 13255.3), 3: (48552.69, 116022.2, 13005.6), 4: (48888.42, 122983.3, 13367.1),
            5: (45638.75, 123478.2, 14604.3), 21: (49314.27, 114616.8, 13553.9),
        ]
        for (seed, (stars, blades, fireflies)) in mockup {
            let sum = sums(MeadowSceneSeeds(seed: seed))
            XCTAssertEqual(sum.stars, stars, accuracy: 1e-6, "seed \(seed)'s stars")
            XCTAssertEqual(sum.blades, blades, accuracy: 1e-6, "seed \(seed)'s grass")
            XCTAssertEqual(sum.fireflies, fireflies, accuracy: 1e-6, "seed \(seed)'s fireflies")
        }
    }

    func testNoSeedIsSeedEleven() {
        XCTAssertEqual(MeadowSceneSeeds(seed: 0), MeadowSceneSeeds(seed: 11))
    }

    func testTheSkysStarsAreTheMockups() {
        let stars = MeadowSceneSeeds.skyStars
        XCTAssertEqual(stars.count, 110)
        XCTAssertEqual(stars.first, MeadowStar(x: 0, y: 263, r: 1.16, opacity: 0.43))
        XCTAssertEqual(stars.last, MeadowStar(x: 281, y: 59, r: 1.28, opacity: 0.52))
        XCTAssertEqual(stars.reduce(0) { $0 + $1.x + $1.y + $1.r + $1.opacity }, 72412.87, accuracy: 1e-6)
    }

    // MARK: Crops and shapes

    func testEachCallersCropIsTheMockups() {
        func check(_ size: CGSize, _ alignment: Alignment, scale: Double, origin: CGPoint,
                   line: UInt = #line) {
            let crop = MeadowCrop.scene(size, alignment)
            XCTAssertEqual(crop.scale, scale, accuracy: 1e-9, line: line)
            XCTAssertEqual(crop.origin.x, origin.x, accuracy: 1e-9, line: line)
            XCTAssertEqual(crop.origin.y, origin.y, accuracy: 1e-9, line: line)
        }
        // The brief's header is wide: cut at the top, pinned to the bottom.
        check(CGSize(width: 972, height: 220), .bottomTrailing, scale: 0.81, origin: CGPoint(x: 0, y: -39.2))
        // The empty pane and the brief's row are narrow: cut at the left, pinned to the right.
        check(CGSize(width: 560, height: 220), .bottomTrailing, scale: 0.6875, origin: CGPoint(x: -265, y: 0))
        check(CGSize(width: 34, height: 34), .bottomTrailing, scale: 0.10625, origin: CGPoint(x: -93.5, y: 0))
        // The gate's header is xMidYMax: pinned to the bottom, and centred when it is narrow.
        let wide = 1352.0 / 1200
        check(CGSize(width: 1352, height: 210), .bottom, scale: wide, origin: CGPoint(x: 0, y: 210 - 320 * wide))
        check(CGSize(width: 600, height: 210), .bottom, scale: 0.65625, origin: CGPoint(x: -93.75, y: 0))
        // Nothing to fill draws nothing.
        check(.zero, .bottom, scale: 0, origin: .zero)
    }

    func testTheSkysBandSpansTwoWindows() {
        let (x, y) = MeadowSkyLayer.bandScale(1440)
        XCTAssertEqual(x * 3600, 2880, accuracy: 1e-9)
        XCTAssertEqual(y * 240, 200, accuracy: 1e-9)
    }

    func testPathDataReadsAsTheMockupsShapes() {
        let shapes = [
            MeadowShapes.mountain, MeadowShapes.hillFar, MeadowShapes.hillMid, MeadowShapes.hillNear,
            MeadowShapes.roof, MeadowShapes.lane, MeadowShapes.trunk, MeadowShapes.branches, MeadowShapes.gateRail,
            MeadowShapes.fencePosts, MeadowShapes.fenceRails, MeadowShapes.gateArch, MeadowShapes.gateBars,
            MeadowShapes.gateStiles, MeadowShapes.birds,
        ]
        for data in shapes { XCTAssertNoThrow(try SVGPath.parse(data), data) }
        XCTAssertEqual(meadowPath(MeadowShapes.mountain).boundingBoxOfPath.maxX, 1200)
        XCTAssertEqual(meadowPath(MeadowShapes.roof).boundingBoxOfPath, CGRect(x: -18, y: -28, width: 38, height: 14))
        // Relative lines, after a move each.
        XCTAssertEqual(meadowPath(MeadowShapes.fencePosts).boundingBoxOfPath,
                       CGRect(x: -150, y: -8, width: 300, height: 30))
        XCTAssertEqual(meadowPath(MeadowShapes.fenceRails).boundingBoxOfPath,
                       CGRect(x: -152, y: 0, width: 304, height: 10))
        // Relative curves: the birds' wings peak half way up their control points.
        XCTAssertEqual(meadowPath(MeadowShapes.birds).boundingBoxOfPath,
                       CGRect(x: 820, y: 89.5, width: 42, height: 14.5))
        // Numbers after a move are lines; a relative move starts from where the last subpath closed.
        XCTAssertEqual(meadowPath("M0 0 10 0 10 10z").boundingBoxOfPath, CGRect(x: 0, y: 0, width: 10, height: 10))
        XCTAssertEqual(meadowPath("M0 0 h4 v4z m3 3 h10 v10 H3z").boundingBoxOfPath,
                       CGRect(x: 0, y: 0, width: 13, height: 13))
    }

    // MARK: The grain and the pictures

    func testTheGrainTilesSeamlessly() {
        let turbulence = MeadowTurbulence(seed: 0)
        let tile = CGRect(x: 0, y: 0, width: 180, height: 180)
        func grain(_ x: Double, _ y: Double, _ tile: CGRect?) -> Double {
            turbulence.value(3, x: x, y: y, frequency: (0.85, 0.85), octaves: 2, tile: tile)
        }
        // The tile's right edge meets its left, and its bottom its top, so the paper shows no seams.
        let edge = 180 - 1e-7
        for along in [0.5, 37.25, 101.75, 179.5] {
            XCTAssert((0...1).contains(grain(along, along, tile)))
            XCTAssertEqual(grain(edge, along, tile), grain(0, along, tile), accuracy: 1e-5)
            XCTAssertEqual(grain(along, edge, tile), grain(along, 0, tile), accuracy: 1e-5)
        }
        // Unstitched, they don't.
        XCTAssertGreaterThan(abs(grain(edge, 37.25, nil) - grain(0, 37.25, nil)), 0.01)
        XCTAssertEqual(MeadowNoise.paper(MeadowPalette.day.grain, scale: 2).width, 360)
        XCTAssertEqual(MeadowNoise.paper(MeadowPalette.night.grain, scale: 1).height, 180)
        XCTAssertEqual(MeadowNoise.sceneTexture.width, 128)
    }

    func testAResizeRedrawsOnlyByAStep() {
        XCTAssertEqual(MeadowArt.quantized(2), 2, accuracy: 1e-12)
        XCTAssertEqual(MeadowArt.quantized(2.05), 2, accuracy: 1e-12)
        XCTAssertNotEqual(MeadowArt.quantized(2.1), 2)
        XCTAssertEqual(MeadowArt.quantized(0), 1)
    }

    /// The alpha of a picture the scene made, at a point of it (its own RGBA, premultiplied last).
    private func alpha(_ image: CGImage, _ x: Int, _ y: Int) throws -> UInt8 {
        let data = try XCTUnwrap(image.dataProvider?.data as Data?)
        return data[y * image.bytesPerRow + x * 4 + 3]
    }

    func testThePictureAtRestIsRightSideUp() throws {
        for light in MeadowLight.allCases {
            for gate in [false, true] {
                let plan = MeadowScenePlan(seed: gate ? 5 : 21, gate: gate,
                                           colors: MeadowSceneColors(MeadowPalette.of(light)),
                                           size: CGSize(width: 1200, height: 320), alignment: .bottom,
                                           displayScale: 1)
                let image = try XCTUnwrap(MeadowSceneLayer.picture(plan))
                XCTAssertEqual(image.width, 1200)
                // The front's top left is open sky, its bottom the near meadow.
                XCTAssertEqual(try alpha(image, 2, 2), 0, "\(light)")
                XCTAssertEqual(try alpha(image, 2, 318), 255, "\(light)")
            }
        }
        let none = MeadowScenePlan(seed: 3, gate: false, colors: MeadowSceneColors(.day), size: .zero,
                                   alignment: .bottomTrailing, displayScale: 2)
        XCTAssertNil(MeadowSceneLayer.picture(none))
    }

    // MARK: Motion

    private func animations(_ layer: CALayer) -> Int {
        (layer.animationKeys()?.count ?? 0) + (layer.sublayers ?? []).reduce(0) { $0 + animations($1) }
    }

    func testReduceMotionStopsTheScenery() {
        let night = MeadowScenePlan(seed: 21, gate: false, colors: MeadowSceneColors(.night),
                                    size: CGSize(width: 972, height: 220), alignment: .bottomTrailing,
                                    displayScale: 2)
        let scene = MeadowSceneLayer()
        scene.apply(night, moving: true)
        // The clouds, three groups of grass, the birds and twelve fireflies.
        XCTAssertEqual(animations(scene), 17)
        scene.apply(night, moving: false)
        XCTAssertEqual(animations(scene), 0)

        // A change of light while still leaves nothing moving; by day there are no fireflies.
        scene.apply(night, moving: true)
        var day = night
        day.colors = MeadowSceneColors(.day)
        scene.apply(day, moving: false)
        XCTAssertEqual(animations(scene), 0)
        scene.apply(day, moving: true)
        XCTAssertEqual(animations(scene), 5)

        let sky = MeadowSkyLayer()
        sky.apply(MeadowSceneColors(.night), size: CGSize(width: 1440, height: 900), displayScale: 2, moving: true)
        XCTAssertEqual(animations(sky), 1)
        sky.apply(MeadowSceneColors(.night), size: CGSize(width: 1440, height: 900), displayScale: 2, moving: false)
        XCTAssertEqual(animations(sky), 0)
    }

    // MARK: Drawn as a view

    func testEachLightDrawsWithoutAWindow() throws {
        if Self.writing { try FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true) }
        for light in MeadowLight.allCases {
            let views: [(String, CGSize, AnyView)] = [
                ("sky", CGSize(width: 1440, height: 900), AnyView(MeadowSky())),
                ("brief", CGSize(width: 972, height: 220), AnyView(MeadowScene(seed: 21))),
                ("empty", CGSize(width: 560, height: 220), AnyView(MeadowScene(seed: 3))),
                ("thumb", CGSize(width: 34, height: 34), AnyView(MeadowScene(seed: 4))),
                ("gate", CGSize(width: 1352, height: 210),
                 AnyView(MeadowScene(seed: 5, gate: true, alignment: .bottom))),
                ("grain", CGSize(width: 360, height: 360),
                 AnyView(ZStack { MeadowPalette.of(light).paper; MeadowGrain() })),
            ]
            for (name, size, view) in views {
                let renderer = ImageRenderer(content: view.frame(width: size.width, height: size.height)
                    .meadowLight(light))
                renderer.scale = 2
                let image = try XCTUnwrap(renderer.cgImage, "\(name), \(light)")
                XCTAssertEqual(image.width, Int(size.width * 2))
                guard Self.writing else { continue }
                let png = try XCTUnwrap(NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]))
                try png.write(to: Self.folder.appending(path: "meadow-\(name)-\(light).png"))
            }
        }
    }
}
