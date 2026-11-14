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
        }
        .defaultSize(width: 640, height: 720)
    }
}

struct AcknowledgementsView: View {
    let text: String?

    var body: some View {
        if let text {
            ReadOnlyText(text: text)
        } else {
            ContentUnavailableView(
                "No acknowledgements in this build", systemImage: "doc.text",
                description: Text("A release carries them: just apple release puts them in the app."))
        }
    }
}

/// A text view to read and copy from, never to edit: 400 KB of licences is more than SwiftUI's Text lays out well.
private struct ReadOnlyText: NSViewRepresentable {
    let text: String

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        guard let view = scroll.documentView as? NSTextView else { return scroll }
        view.isEditable = false
        view.isSelectable = true
        view.isRichText = false
        view.drawsBackground = true
        view.backgroundColor = .textBackgroundColor
        view.textColor = .textColor
        view.font = .monospacedSystemFont(ofSize: NSFont.smallSystemFontSize, weight: .regular)
        view.textContainerInset = NSSize(width: 12, height: 12)
        view.string = text
        view.setAccessibilityLabel("Acknowledgements")
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {}
}
#else
/// iPhone and iPad have no menu bar; their acknowledgements come with a release of their own.
struct AcknowledgementsCommands: Commands {
    var body: some Commands { EmptyCommands() }
}
#endif
