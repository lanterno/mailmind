import CoreGraphics
import Foundation
import SwiftUI

// SVG's path data, read into a SwiftUI `Path` (#44): Meadow's icons are the mockup's own <symbol>s, kept as the text
// the page draws them from, so an icon changed in one is pasted into the other unchanged.

/// The shapes an icon's <symbol> is made of, in the symbol's 24-point grid.
enum SVGPart: Sendable, Equatable {
    /// A <path>'s `d`.
    case path(String)
    /// A <circle>: its centre and radius.
    case circle(CGFloat, CGFloat, CGFloat)
    /// A <rect>: its origin, size and corner radius (`rx`).
    case rect(CGFloat, CGFloat, CGFloat, CGFloat, CGFloat)
}

/// Path data SVG can't read, and where in it the reading stopped.
struct SVGPathError: Error, Equatable, CustomStringConvertible {
    let data: String
    let offset: Int

    var description: String { "SVG path data unreadable at \(offset): \(data)" }
}

enum SVGPath {
    /// The parts as one path.
    static func path(_ parts: [SVGPart]) throws -> Path {
        var path = Path()
        for part in parts {
            switch part {
            case let .path(data):
                path.addPath(try parse(data))
            case let .circle(x, y, r):
                path.addEllipse(in: CGRect(x: x - r, y: y - r, width: 2 * r, height: 2 * r))
            case let .rect(x, y, width, height, radius):
                let rect = CGRect(x: x, y: y, width: width, height: height)
                path.addRoundedRect(in: rect, cornerSize: CGSize(width: radius, height: radius), style: .circular)
            }
        }
        return path
    }

    /// Path data as SVG reads it (SVG 1.1, 8.3): every command absolute and relative, a command's numbers repeated
    /// without it, and an elliptical arc drawn as the cubic curves that follow it.
    static func parse(_ data: String) throws -> Path {
        var reader = Reader(data)
        var path = Path()
        var current = CGPoint.zero
        var start = CGPoint.zero
        // The last curve's second control point, which S and T reflect: a cubic's for S, a quadratic's for T.
        var cubic: CGPoint?
        var quadratic: CGPoint?
        // After Z, the next command starts from the subpath's first point.
        var closed = false
        var started = false

        while let letter = try reader.command() {
            if letter == "Z" || letter == "z" {
                path.closeSubpath()
                current = start
                cubic = nil
                quadratic = nil
                closed = true
                continue
            }
            // Path data starts with a moveto (SVG 1.1, 8.3.2).
            if !started && letter != "M" && letter != "m" { throw reader.error }
            started = true
            var command = letter
            repeat {
                let relative = command.isLowercase
                let origin = relative ? current : .zero
                func point() throws -> CGPoint {
                    let x = try reader.number()
                    let y = try reader.number()
                    return CGPoint(x: origin.x + x, y: origin.y + y)
                }
                if closed && command != "M" && command != "m" { path.move(to: current) }
                closed = false
                var nextCubic: CGPoint?
                var nextQuadratic: CGPoint?

                switch command {
                case "M", "m":
                    current = try point()
                    start = current
                    path.move(to: current)
                    cubic = nil
                    quadratic = nil
                    // Numbers after a moveto are linetos (SVG 1.1, 8.3.2).
                    command = relative ? "l" : "L"
                    continue
                case "L", "l":
                    current = try point()
                    path.addLine(to: current)
                case "H", "h":
                    current.x = (relative ? current.x : 0) + (try reader.number())
                    path.addLine(to: current)
                case "V", "v":
                    current.y = (relative ? current.y : 0) + (try reader.number())
                    path.addLine(to: current)
                case "C", "c":
                    let one = try point()
                    let two = try point()
                    current = try point()
                    path.addCurve(to: current, control1: one, control2: two)
                    nextCubic = two
                case "S", "s":
                    let one = cubic.map { reflect($0, about: current) } ?? current
                    let two = try point()
                    current = try point()
                    path.addCurve(to: current, control1: one, control2: two)
                    nextCubic = two
                case "Q", "q":
                    let control = try point()
                    current = try point()
                    path.addQuadCurve(to: current, control: control)
                    nextQuadratic = control
                case "T", "t":
                    let control = quadratic.map { reflect($0, about: current) } ?? current
                    current = try point()
                    path.addQuadCurve(to: current, control: control)
                    nextQuadratic = control
                case "A", "a":
                    let rx = try reader.number()
                    let ry = try reader.number()
                    let rotation = try reader.number()
                    let large = try reader.flag()
                    let sweep = try reader.flag()
                    let end = try point()
                    arc(&path, from: current, to: end, rx: rx, ry: ry, rotation: rotation, large: large, sweep: sweep)
                    current = end
                default:
                    throw reader.error
                }
                cubic = nextCubic
                quadratic = nextQuadratic
            } while reader.atNumber
        }
        return path
    }

    private static func reflect(_ point: CGPoint, about centre: CGPoint) -> CGPoint {
        CGPoint(x: 2 * centre.x - point.x, y: 2 * centre.y - point.y)
    }

    /// An elliptical arc as cubic curves of at most a quarter turn each, from its endpoints as SVG gives them to its
    /// centre (SVG 1.1, F.6.5 and F.6.6).
    static func arc(_ path: inout Path, from p1: CGPoint, to p2: CGPoint, rx: CGFloat, ry: CGFloat,
                    rotation: CGFloat, large: Bool, sweep: Bool) {
        if p1 == p2 { return }
        var rx = abs(rx)
        var ry = abs(ry)
        if rx == 0 || ry == 0 {
            path.addLine(to: p2)
            return
        }
        let phi = rotation * .pi / 180
        let cosPhi = cos(phi)
        let sinPhi = sin(phi)
        let dx = (p1.x - p2.x) / 2
        let dy = (p1.y - p2.y) / 2
        let x1 = cosPhi * dx + sinPhi * dy
        let y1 = -sinPhi * dx + cosPhi * dy
        // Radii too small to reach are scaled up until they just do.
        let lambda = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
        if lambda > 1 {
            rx *= lambda.squareRoot()
            ry *= lambda.squareRoot()
        }
        let numerator = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
        let denominator = rx * rx * y1 * y1 + ry * ry * x1 * x1
        let coefficient = (large == sweep ? -1 : 1) * max(0, numerator / denominator).squareRoot()
        let cx1 = coefficient * rx * y1 / ry
        let cy1 = -coefficient * ry * x1 / rx
        let centre = CGPoint(x: cosPhi * cx1 - sinPhi * cy1 + (p1.x + p2.x) / 2,
                             y: sinPhi * cx1 + cosPhi * cy1 + (p1.y + p2.y) / 2)

        func angle(_ ux: CGFloat, _ uy: CGFloat, _ vx: CGFloat, _ vy: CGFloat) -> CGFloat {
            atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        }
        let theta = angle(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
        var delta = angle((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
        if !sweep && delta > 0 { delta -= 2 * .pi }
        if sweep && delta < 0 { delta += 2 * .pi }

        // A point of the unit circle, on the ellipse.
        func onEllipse(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: centre.x + rx * cosPhi * x - ry * sinPhi * y, y: centre.y + rx * sinPhi * x + ry * cosPhi * y)
        }
        // A hair under a quarter turn's worth, so a turn of exactly 90 degrees is one curve and not two.
        let segments = max(1, Int((abs(delta) / (.pi / 2) - 1e-9).rounded(.up)))
        let step = delta / CGFloat(segments)
        let k = 4 / 3 * tan(step / 4)
        var a = theta
        for index in 0..<segments {
            let b = a + step
            let control1 = onEllipse(cos(a) - k * sin(a), sin(a) + k * cos(a))
            let control2 = onEllipse(cos(b) + k * sin(b), sin(b) - k * cos(b))
            let end = index == segments - 1 ? p2 : onEllipse(cos(b), sin(b))
            path.addCurve(to: end, control1: control1, control2: control2)
            a = b
        }
    }
}

/// Path data's commands, numbers and flags, in order (SVG 1.1, 8.3.9).
private struct Reader {
    private let bytes: [UInt8]
    private let data: String
    private var index = 0

    init(_ data: String) {
        self.data = data
        bytes = Array(data.utf8)
    }

    var error: SVGPathError { SVGPathError(data: data, offset: index) }

    private mutating func skip() {
        while index < bytes.count, [0x20, 0x09, 0x0A, 0x0D, 0x0C, 0x2C].contains(bytes[index]) { index += 1 }
    }

    private func isDigit(_ at: Int) -> Bool { at < bytes.count && bytes[at] >= 0x30 && bytes[at] <= 0x39 }

    /// Whether a number comes next, which repeats the command before it.
    var atNumber: Bool {
        mutating get {
            skip()
            guard index < bytes.count else { return false }
            let byte = bytes[index]
            return isDigit(index) || byte == 0x2E || byte == 0x2D || byte == 0x2B
        }
    }

    /// The next command's letter, or nil at the end. A number where a command should be is an error.
    mutating func command() throws -> Character? {
        skip()
        guard index < bytes.count else { return nil }
        let letter = Character(UnicodeScalar(bytes[index]))
        guard "MmZzLlHhVvCcSsQqTtAa".contains(letter) else { throw error }
        index += 1
        return letter
    }

    mutating func number() throws -> CGFloat {
        skip()
        let start = index
        if index < bytes.count, bytes[index] == 0x2D || bytes[index] == 0x2B { index += 1 }
        var digits = 0
        while isDigit(index) { index += 1; digits += 1 }
        if index < bytes.count, bytes[index] == 0x2E {
            index += 1
            while isDigit(index) { index += 1; digits += 1 }
        }
        guard digits > 0 else {
            index = start
            throw error
        }
        // An exponent only when digits follow it, so "1e" stays a number then whatever "e" is.
        if index < bytes.count, bytes[index] == 0x65 || bytes[index] == 0x45 {
            var at = index + 1
            if at < bytes.count, bytes[at] == 0x2D || bytes[at] == 0x2B { at += 1 }
            if isDigit(at) {
                index = at
                while isDigit(index) { index += 1 }
            }
        }
        guard let value = Double(String(decoding: bytes[start..<index], as: UTF8.self)) else {
            index = start
            throw error
        }
        return CGFloat(value)
    }

    /// An arc's flag: one character, 0 or 1, which may run straight into the next number ("a1 1 0 01.5 2").
    mutating func flag() throws -> Bool {
        skip()
        guard index < bytes.count, bytes[index] == 0x30 || bytes[index] == 0x31 else { throw error }
        defer { index += 1 }
        return bytes[index] == 0x31
    }
}
