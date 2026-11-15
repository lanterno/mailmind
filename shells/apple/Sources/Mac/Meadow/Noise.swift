import Accelerate
import CoreGraphics
import Foundation
import SwiftUI

// The noise under Meadow's paper and paint (#44): SVG's feTurbulence, ported from the reference code in the Filter
// Effects spec with the stitching browsers actually do, so the grain and the hills' painted wobble come from the same
// numbers the mockup's do.

/// One feTurbulence: a seed's lattice and gradients, evaluated as `type="fractalNoise"`.
struct MeadowTurbulence: Sendable {
    private static let size = 0x100, mask = 0xFF, offset = 0x1000
    private static let randM = 2147483647, randA = 16807, randQ = 127773, randR = 2836

    /// The lattice selector, `2 * size + 2` long.
    private let lattice: [Int]
    /// The gradients, by channel, then lattice point, then x and y.
    private let gradient: [Double]

    init(seed: Int) {
        var seed = seed
        if seed <= 0 { seed = -(seed % (Self.randM - 1)) + 1 }
        if seed > Self.randM - 1 { seed = Self.randM - 1 }
        func random() -> Int {
            seed = Self.randA * (seed % Self.randQ) - Self.randR * (seed / Self.randQ)
            if seed <= 0 { seed += Self.randM }
            return seed
        }
        let n = Self.size, points = n + n + 2
        var lattice = [Int](repeating: 0, count: points)
        var gradient = [Double](repeating: 0, count: 4 * points * 2)
        for channel in 0..<4 {
            for i in 0..<n {
                lattice[i] = i
                let at = (channel * points + i) * 2
                gradient[at] = Double((random() % (n + n)) - n) / Double(n)
                gradient[at + 1] = Double((random() % (n + n)) - n) / Double(n)
                let length = (gradient[at] * gradient[at] + gradient[at + 1] * gradient[at + 1]).squareRoot()
                if length > 0 {
                    gradient[at] /= length
                    gradient[at + 1] /= length
                }
            }
        }
        var i = n - 1
        while i > 0 {
            let k = lattice[i], j = random() % n
            lattice[i] = lattice[j]
            lattice[j] = k
            i -= 1
        }
        for i in 0..<(n + 2) {
            lattice[n + i] = lattice[i]
            for channel in 0..<4 {
                let from = (channel * points + i) * 2, to = (channel * points + n + i) * 2
                gradient[to] = gradient[from]
                gradient[to + 1] = gradient[from + 1]
            }
        }
        self.lattice = lattice
        self.gradient = gradient
    }

    /// How a tile wraps the lattice when `stitchTiles="stitch"`: its width and height in lattice cells, and where
    /// the wrap falls.
    struct Stitch {
        var width: Int, height: Int, wrapX: Int, wrapY: Int
    }

    /// The spec's `turbulence()` for fractal noise, mapped to a colour value: 0 to 1, a half at rest.
    /// - Parameters:
    ///   - channel: 0 red, 1 green, 2 blue, 3 alpha.
    ///   - tile: the tile to stitch, or nil to leave the noise unstitched.
    func value(_ channel: Int, x: Double, y: Double, frequency: (Double, Double), octaves: Int,
               tile: CGRect? = nil) -> Double {
        var fx = frequency.0, fy = frequency.1
        var stitch: Stitch?
        if let tile {
            (fx, fy) = Self.stitched(frequency, tile: tile)
            let width = Int(tile.width * fx + 0.5), height = Int(tile.height * fy + 0.5)
            stitch = Stitch(width: width, height: height, wrapX: Int(tile.minX * fx) + Self.offset + width,
                            wrapY: Int(tile.minY * fy) + Self.offset + height)
        }
        var sum = 0.0, vx = x * fx, vy = y * fy, ratio = 1.0
        lattice.withUnsafeBufferPointer { lattice in
            gradient.withUnsafeBufferPointer { gradient in
                for _ in 0..<octaves {
                    sum += Self.noise2(channel, vx, vy, stitch, lattice, gradient) / ratio
                    vx *= 2
                    vy *= 2
                    ratio *= 2
                    if stitch != nil {
                        stitch!.width *= 2
                        stitch!.wrapX = 2 * stitch!.wrapX - Self.offset
                        stitch!.height *= 2
                        stitch!.wrapY = 2 * stitch!.wrapY - Self.offset
                    }
                }
            }
        }
        return min(max((sum + 1) / 2, 0), 1)
    }

    /// The base frequencies a stitched tile uses: each moved to the nearest that fits a whole number of cells.
    static func stitched(_ frequency: (Double, Double), tile: CGRect) -> (Double, Double) {
        func fit(_ f: Double, _ length: Double) -> Double {
            guard f != 0 else { return f }
            let low = (length * f).rounded(.down) / length, high = (length * f).rounded(.up) / length
            return f / low < high / f ? low : high
        }
        return (fit(frequency.0, tile.width), fit(frequency.1, tile.height))
    }

    private static func noise2(_ channel: Int, _ x: Double, _ y: Double, _ stitch: Stitch?,
                               _ lattice: UnsafeBufferPointer<Int>, _ gradient: UnsafeBufferPointer<Double>) -> Double {
        let tx = x + Double(offset), ty = y + Double(offset)
        var bx0 = Int(tx), by0 = Int(ty)
        var bx1 = bx0 + 1, by1 = by0 + 1
        let rx0 = tx - Double(Int(tx)), ry0 = ty - Double(Int(ty))
        let rx1 = rx0 - 1, ry1 = ry0 - 1
        if let stitch {
            if bx0 >= stitch.wrapX { bx0 -= stitch.width }
            if bx1 >= stitch.wrapX { bx1 -= stitch.width }
            if by0 >= stitch.wrapY { by0 -= stitch.height }
            if by1 >= stitch.wrapY { by1 -= stitch.height }
        }
        bx0 &= mask
        bx1 &= mask
        by0 &= mask
        by1 &= mask
        let i = lattice[bx0], j = lattice[bx1]
        let b00 = lattice[i + by0], b10 = lattice[j + by0], b01 = lattice[i + by1], b11 = lattice[j + by1]
        let sx = rx0 * rx0 * (3 - 2 * rx0), sy = ry0 * ry0 * (3 - 2 * ry0)
        let base = channel * (size + size + 2)
        func dot(_ b: Int, _ rx: Double, _ ry: Double) -> Double {
            rx * gradient[(base + b) * 2] + ry * gradient[(base + b) * 2 + 1]
        }
        let a = dot(b00, rx0, ry0) + sx * (dot(b10, rx1, ry0) - dot(b00, rx0, ry0))
        let b = dot(b01, rx0, ry1) + sx * (dot(b11, rx1, ry1) - dot(b01, rx0, ry1))
        return a + sy * (b - a)
    }
}

/// The grain tiles, made once and kept: the paper's (`--grain`) and the scene's finer texture (its `tex` filter).
enum MeadowNoise {
    /// The paper grain's alpha at each pixel of a 180-point tile at `scale` pixels a point: feTurbulence at .85, two
    /// octaves, stitched, its alpha channel. The light only colours it.
    private static let paperAlpha = Memo<Int, [UInt8]> { scale in
        let side = 180 * scale
        let turbulence = MeadowTurbulence(seed: 0)
        let tile = CGRect(x: 0, y: 0, width: 180, height: 180)
        var alpha = [UInt8](repeating: 0, count: side * side)
        for row in 0..<side {
            for column in 0..<side {
                let x = (Double(column) + 0.5) / Double(scale), y = (Double(row) + 0.5) / Double(scale)
                let value = turbulence.value(3, x: x, y: y, frequency: (0.85, 0.85), octaves: 2, tile: tile)
                alpha[row * side + column] = UInt8((value * 255).rounded())
            }
        }
        return alpha
    }

    private static let paperTiles = Memo<GrainKey, CGImage> { key in
        let alpha = paperAlpha[key.scale]
        let side = 180 * key.scale
        return tile(side: side, alpha: alpha, color: key.color, strength: key.strength)
    }

    private struct GrainKey: Hashable {
        let color: [Double], strength: Double, scale: Int
    }

    /// The paper's grain tile for a light's grain colour, 180 points square at `scale` pixels a point.
    ///
    /// The mockup's colour matrix works in linear light, as SVG filters do by default, so the colour's numbers are
    /// read as linear: the page's grain is a lighter, warmer brown than the same numbers in sRGB.
    static func paper(_ color: Color, scale: Int) -> CGImage {
        let resolved = color.resolve(in: EnvironmentValues())
        let key = GrainKey(color: [Double(resolved.red), Double(resolved.green), Double(resolved.blue)],
                           strength: Double(resolved.opacity), scale: max(1, scale))
        return paperTiles[key]
    }

    /// The scene's texture, a darkening as its multiplied `tex` rect gives: black, at the alpha that multiplying by
    /// the filter's grey (linear .3, .3, .25) at half of .22 opacity takes away. Its noise is per pixel, as the
    /// browser's is at every size the scene is drawn: 128 pixels square, to tile in device pixels.
    static let sceneTexture: CGImage = {
        let side = 128
        let turbulence = MeadowTurbulence(seed: 9)
        let tileRect = CGRect(x: 0, y: 0, width: side, height: side)
        let grey = linearToSRGB(0.3)
        var alpha = [UInt8](repeating: 0, count: side * side)
        for row in 0..<side {
            for column in 0..<side {
                let value = turbulence.value(3, x: Double(column) + 0.5, y: Double(row) + 0.5, frequency: (0.5, 0.5),
                                             octaves: 2, tile: tileRect)
                alpha[row * side + column] = UInt8((value * 0.5 * 0.22 * (1 - grey) * 255).rounded())
            }
        }
        return tile(side: side, alpha: alpha, color: nil, strength: 1)
    }()

    /// A tile of `color` (linear light; black when nil) whose alpha is `alpha` times `strength`, premultiplied.
    private static func tile(side: Int, alpha: [UInt8], color: [Double]?, strength: Double) -> CGImage {
        let rgb = (color ?? [0, 0, 0]).map(linearToSRGB)
        var pixels = [UInt8](repeating: 0, count: side * side * 4)
        for index in 0..<(side * side) {
            let a = Double(alpha[index]) / 255 * strength
            pixels[index * 4] = UInt8((rgb[0] * a * 255).rounded())
            pixels[index * 4 + 1] = UInt8((rgb[1] * a * 255).rounded())
            pixels[index * 4 + 2] = UInt8((rgb[2] * a * 255).rounded())
            pixels[index * 4 + 3] = UInt8((a * 255).rounded())
        }
        let provider = CGDataProvider(data: Data(pixels) as CFData)!
        let info = CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue)
        return CGImage(width: side, height: side, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: side * 4,
                       space: MeadowArt.space, bitmapInfo: info, provider: provider, decode: nil,
                       shouldInterpolate: false, intent: .defaultIntent)!
    }

    /// A linear-light value in sRGB's encoding.
    static func linearToSRGB(_ value: Double) -> Double {
        value <= 0.0031308 ? 12.92 * value : 1.055 * pow(value, 1 / 2.4) - 0.055
    }
}

/// What a closure makes for a key, made once and kept, from any thread.
final class Memo<Key: Hashable, Value>: @unchecked Sendable {
    private let make: (Key) -> Value
    private var made: [Key: Value] = [:]
    private let lock = NSLock()

    init(_ make: @escaping (Key) -> Value) { self.make = make }

    subscript(key: Key) -> Value {
        lock.lock()
        defer { lock.unlock() }
        if let value = made[key] { return value }
        let value = make(key)
        made[key] = value
        return value
    }
}

/// A Gaussian blur of a premultiplied bitmap context's pixels in place, `sigma` pixels wide, as feGaussianBlur's.
func meadowBlur(_ context: CGContext, sigma: Double) {
    guard sigma > 0.1, let data = context.data else { return }
    let radius = Int((sigma * 3).rounded(.up))
    var kernel = (-radius...radius).map { Float(exp(-Double($0 * $0) / (2 * sigma * sigma))) }
    let total = kernel.reduce(0, +)
    kernel = kernel.map { $0 / total }
    var buffer = vImage_Buffer(data: data, height: vImagePixelCount(context.height),
                               width: vImagePixelCount(context.width), rowBytes: context.bytesPerRow)
    var out = [UInt8](repeating: 0, count: context.bytesPerRow * context.height)
    out.withUnsafeMutableBytes { raw in
        var dest = vImage_Buffer(data: raw.baseAddress, height: buffer.height, width: buffer.width,
                                 rowBytes: context.bytesPerRow)
        let clear: [UInt8] = [0, 0, 0, 0]
        kernel.withUnsafeBufferPointer { k in
            _ = vImageSepConvolve_ARGB8888(&buffer, &dest, nil, 0, 0, k.baseAddress!, UInt32(k.count),
                                           k.baseAddress!, UInt32(k.count), 0, clear,
                                           vImage_Flags(kvImageBackgroundColorFill))
        }
        memcpy(data, raw.baseAddress, raw.count)
    }
}
