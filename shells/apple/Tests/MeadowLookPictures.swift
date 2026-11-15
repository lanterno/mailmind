import AppKit
import SwiftUI
import XCTest

/// Every icon at the mockup's two sizes, and the type specimen from its design notes, drawn in each light: each must
/// draw, and with `TEST_RUNNER_MEADOW_PICTURES=<folder>` given to `xcodebuild test` each is written there, at twice
/// the size, to be laid beside the mockup's pictures.
@MainActor
final class MeadowLookPictures: XCTestCase {
    private static var folder: URL? {
        ProcessInfo.processInfo.environment["MEADOW_PICTURES"].map { URL(fileURLWithPath: $0) }
    }

    private func draw(_ name: String, _ view: some View) throws {
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        let image = try XCTUnwrap(renderer.cgImage, name)
        XCTAssertGreaterThan(image.width * image.height, 10_000, name)
        guard let folder = Self.folder else { return }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let png = try XCTUnwrap(NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]))
        try png.write(to: folder.appendingPathComponent("\(name).png"))
    }

    func testTheIconSheet() throws {
        for light in MeadowLight.allCases {
            try draw("icons-\(light.rawValue)", IconSheet().meadowLight(light))
        }
    }

    func testTheTypeSpecimen() throws {
        for light in MeadowLight.allCases {
            try draw("type-\(light.rawValue)", TypeSpecimen().meadowLight(light))
        }
    }

    func testTheRailBesideTheMockups() throws {
        // The rail's icons in the mockup's order, at its size and colour, to lay over reference/1440-*-brief.png.
        for light in MeadowLight.allCases {
            try draw("rail-\(light.rawValue)", Rail().meadowLight(light))
        }
    }
}

/// Every icon at 18 and 15 points, named, ink on paper.
private struct IconSheet: View {
    @Environment(\.meadow) private var palette

    var body: some View {
        let columns = Array(repeating: GridItem(.fixed(76), spacing: 6), count: 10)
        LazyVGrid(columns: columns, alignment: .leading, spacing: 14) {
            ForEach(MeadowIcon.allCases, id: \.self) { icon in
                VStack(spacing: 6) {
                    HStack(spacing: 10) {
                        MeadowIconView(icon, size: 18)
                        MeadowIconView(icon, size: 15)
                    }
                    .foregroundStyle(palette.ink2)
                    Text(icon.rawValue)
                        .font(.meadow(.ui, 10, .semibold))
                        .foregroundStyle(palette.ink3)
                }
                .frame(width: 76, height: 48)
                .background(palette.paperRaised, in: RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding(16)
        .frame(width: 16 * 2 + 10 * 76 + 9 * 6)
        .background(palette.paper)
    }
}

/// The design notes' type specimen (the mockup's `.typespec`), and the brief's greeting and voice at their sizes.
private struct TypeSpecimen: View {
    @Environment(\.meadow) private var palette

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            row("Display · Shippori Mincho") {
                Text("Good afternoon, Hana.").font(.meadow(.display, 28, .bold)).foregroundStyle(palette.ink)
            }
            row("Mail & interface · Nunito Sans") {
                Text("Could you sign and send it back by Monday?").font(.meadow(.ui, 17)).foregroundStyle(palette.ink)
            }
            row("The brief's voice · Klee One") {
                Text("A calm Friday. Three things need you.").font(.meadow(.hand, 17, .semibold))
                    .foregroundStyle(palette.moss)
            }
            row("Nunito Sans 400–800") {
                HStack(spacing: 14) {
                    ForEach([Font.Weight.regular, .medium, .semibold, .bold, .heavy], id: \.self) { weight in
                        Text("Inbox").font(.meadow(.ui, 17, weight)).foregroundStyle(palette.ink)
                    }
                    Text("Inbox").font(.meadow(.ui, 17, italic: true)).foregroundStyle(palette.ink)
                }
            }
            row("Hero · 40 / brief · 18") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Good afternoon, Hana.").font(.meadow(.display, 40, .bold)).foregroundStyle(palette.ink)
                    Text("A calm Friday. Three things need you: Ren needs the signed Harbor Library contract")
                        .font(.meadow(.hand, 18, .semibold)).foregroundStyle(palette.moss)
                        .lineSpacing(MeadowFace.hand.lineSpacing(size: 18, lineHeight: 1.7))
                }
            }
        }
        .padding(24)
        .frame(width: 900, alignment: .leading)
        .background(palette.paper)
    }

    private func row(_ label: String, @ViewBuilder _ sample: () -> some View) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            Text(label).font(.meadow(.ui, 12.5)).foregroundStyle(palette.ink3).frame(width: 170, alignment: .leading)
            sample().frame(width: 666, alignment: .leading)
        }
        .padding(.vertical, 10)
        .overlay(alignment: .bottom) { Rectangle().fill(palette.line).frame(height: 1) }
    }
}

/// The rail's icons as the mockup stacks them, 44 points apart, in ink-2 on paper.
private struct Rail: View {
    @Environment(\.meadow) private var palette

    var body: some View {
        VStack(spacing: 26) {
            ForEach([MeadowIcon.sun, .inbox, .gate, .hourglass, .bookmark, .clock, .leaf, .receipt, .archive, .send,
                     .trash, .folder, .book, .sliders], id: \.self) { icon in
                MeadowIconView(icon, size: 18).foregroundStyle(palette.ink2)
            }
        }
        .padding(13)
        .background(palette.paper2)
    }
}
