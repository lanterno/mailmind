import SwiftUI

/// The licences a release carries (Release/acknowledgements.py writes them into the app as Acknowledgements.txt).
enum Acknowledgements {
    static let windowID = "acknowledgements"

    /// The text in `bundle`, or nil in a build that carries none: only `just apple release` puts it there.
    static func text(in bundle: Bundle) -> String? {
        guard let url = bundle.url(forResource: "Acknowledgements", withExtension: "txt") else { return nil }
        return try? String(contentsOf: url, encoding: .utf8)
    }
}

#if os(macOS)
import AppKit

/// "Acknowledgements" in the app menu, after About, where a Mac app keeps them.
struct AcknowledgementsCommands: Commands {
    var body: some Commands {
        CommandGroup(after: .appInfo) {
            OpenAcknowledgements()
        }
    }
}

private struct OpenAcknowledgements: View {
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("Acknowledgements") { openWindow(id: Acknowledgements.windowID) }
    }
}

/// One window, opened only from the menu.
struct AcknowledgementsWindow: Scene {
    var body: some Scene {
        // SwiftUI's, not the core's list window that the bindings name Window too.
        SwiftUI.Window("Acknowledgements", id: Acknowledgements.windowID) {
            AcknowledgementsView(text: Acknowledgements.text(in: .main))
                .meadowLit()
        }
        // Meadow's paper to the window's edges, the traffic lights on its head, as Help's window.
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 640, height: 720)
    }
}

/// The licences on Meadow's paper, under a band the traffic lights stand on, which moves the window as a title bar
/// does.
struct AcknowledgementsView: View {
    let text: String?
    /// Whether the window's traffic lights stand on its paper; a test draws it without a window of its own.
    var lights = true
    /// The band the traffic lights stand in.
    static let barHeight: CGFloat = 40
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(spacing: 0) {
            Color.clear
                .frame(height: Self.barHeight)
                .frame(maxWidth: .infinity)
                .background { WindowDragArea() }
            if let text {
                ReadOnlyText(text: text, ink: NSColor(meadow.ink))
            } else {
                VStack(spacing: 6) {
                    Text("No acknowledgements in this build")
                        .font(.meadow(.display, 20, .bold))
                        .foregroundStyle(meadow.ink)
                    Text("A release carries them: just apple release puts them in the app.")
                        .font(.meadow(.ui, 14))
                        .foregroundStyle(meadow.ink2)
                }
                .multilineTextAlignment(.center)
                .padding(30)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(minWidth: 420, minHeight: 320)
        .background { ZStack { meadow.paper; MeadowGrain() }.ignoresSafeArea() }
        .ignoresSafeArea()
        .meadowWindowChrome(barHeight: Self.barHeight, on: lights)
    }
}

/// A text view to read and copy from, never to edit: 400 KB of licences is more than SwiftUI's Text lays out well. It
/// draws no background of its own, so the paper shows through, and its ink is the light's.
private struct ReadOnlyText: NSViewRepresentable {
    let text: String
    let ink: NSColor

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        scroll.drawsBackground = false
        guard let view = scroll.documentView as? NSTextView else { return scroll }
        view.isEditable = false
        view.isSelectable = true
        view.isRichText = false
        view.drawsBackground = false
        view.textColor = ink
        view.font = .meadow(.mono, 11.5)
        view.textContainerInset = NSSize(width: 28, height: 8)
        view.string = text
        view.setAccessibilityLabel("Acknowledgements")
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        (scroll.documentView as? NSTextView)?.textColor = ink
    }
}
#else
/// iPhone and iPad have no menu bar; their acknowledgements come with a release of their own.
struct AcknowledgementsCommands: Commands {
    var body: some Commands { EmptyCommands() }
}
#endif
