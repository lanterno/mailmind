#if os(macOS)
import AppKit
import SwiftUI
import WebKit

/// Find in Message (⌥⌘F; ⌘F in a message's own window): a word looked for in the open message's body, or each body
/// a conversation shows, as a Mac's find bar does. WebKit finds and selects it in a frame; the frame measures again,
/// as a match in a fold opens it, and the app asks it where the selection is, in its own isolated world
/// (`FrameAsk.world`, never the mail's); then the reader scrolls to it, as the frames are as tall as their content.
@MainActor
@Observable
final class ReaderFind {
    var open = false
    var query = ""
    /// Whether the last look found it: nil before one, or with nothing typed.
    private(set) var found: Bool?
    /// Asked to put the cursor in the field.
    var focusField = false

    @ObservationIgnored private var frames: [Weak] = []
    /// The frame the last match was in, where the next look starts.
    @ObservationIgnored private var at = 0
    /// Counts the looks, so a match a look found before the query changed again doesn't move the reader.
    @ObservationIgnored private var round = 0

    private struct Weak {
        weak var view: WKWebView?
    }

    /// The frames on screen, in the order the reader shows them.
    var shown: [WKWebView] {
        frames = frames.filter { $0.view != nil }
        return frames.compactMap(\.view).filter { $0.window != nil && !$0.isHiddenOrHasHiddenAncestor }
    }

    func add(_ view: WKWebView) {
        guard !frames.contains(where: { $0.view === view }) else { return }
        frames.append(Weak(view: view))
    }

    func remove(_ view: WKWebView) {
        frames.removeAll { $0.view == nil || $0.view === view }
    }

    func show() {
        open = true
        focusField = true
    }

    func close() {
        open = false
        found = nil
        for view in shown { Self.clearSelection(view) }
    }

    /// Look for the query from where the last match was, on through the other frames, and round to the first.
    func next(backwards: Bool = false) async {
        round += 1
        let look = round
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else {
            found = nil
            return
        }
        let views = shown
        guard !views.isEmpty else {
            found = false
            return
        }
        let start = min(at, views.count - 1)
        for step in 0...views.count {
            let index = ((start + (backwards ? -step : step)) % views.count + views.count) % views.count
            let view = views[index]
            // Another frame, or this one again after going round: look from its top (or its foot, going back).
            if step > 0 { Self.clearSelection(view) }
            let configuration = WKFindConfiguration()
            configuration.backwards = backwards
            configuration.caseSensitive = false
            configuration.wraps = views.count == 1
            if let result = try? await view.find(query, configuration: configuration), result.matchFound {
                at = index
                found = true
                await reveal(view, look: look)
                return
            }
        }
        found = false
    }

    /// Scroll the reader to the selection WebKit made, so it sits well inside what the reader shows. A match in a
    /// fold opens it, and WebKit may scroll the frame inside itself to show it: a frame of the reader's measures
    /// again first, its own scroll put back to the top (`FrameGuard.found`). Where the selection is comes from the
    /// app's own world (`FrameAsk.world`), never the mail's.
    private func reveal(_ view: WKWebView, look: Int) async {
        let rect = if let frame = view as? FrameWebView, let walls = frame.walls {
            await walls.found()
        } else {
            FrameMessage.rect(await FrameAsk.said(FrameAsk.selection, of: view))
        }
        guard let rect, look == round else { return }
        view.reveal(rect)
    }

    private static func clearSelection(_ view: WKWebView) {
        Task {
            _ = try? await view.callAsyncJavaScript("window.getSelection()?.removeAllRanges(); return null;",
                                                    arguments: [:], in: nil, contentWorld: FrameAsk.world)
        }
    }
}

extension EnvironmentValues {
    /// The reader's Find, which each frame it shows joins.
    @Entry var readerFind: ReaderFind?
}

/// The find bar over the reader: the field, whether it was found, the previous and next match, and Done.
struct ReaderFindBar: View {
    @Bindable var find: ReaderFind
    @FocusState private var focused: Bool
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 8) {
            MeadowIconView(.search, size: 14)
                .foregroundStyle(meadow.ink3)
            TextField("Find in message", text: $find.query)
                .textFieldStyle(.plain)
                .font(.meadow(.ui, 13.5))
                .foregroundStyle(meadow.ink)
                .focused($focused)
                .onSubmit { Task { await find.next() } }
                .onKeyPress(.return, phases: .down) { press in
                    guard press.modifiers.contains(.shift) else { return .ignored }
                    Task { await find.next(backwards: true) }
                    return .handled
                }
                .onKeyPress(.escape) {
                    find.close()
                    return .handled
                }
                .onChange(of: find.query) { Task { await find.next() } }
            if find.found == false {
                Text("Not found")
                    .font(.meadow(.ui, 12.5, .semibold))
                    .foregroundStyle(meadow.ink3)
            }
            Button { Task { await find.next(backwards: true) } } label: { MeadowIconView(.up) }
                .meadowButton(.quiet, icon: true)
                .accessibilityLabel("Previous match")
                .help("Previous match (⇧↵)")
                .disabled(find.query.isEmpty)
            Button { Task { await find.next() } } label: { MeadowIconView(.down) }
                .meadowButton(.quiet, icon: true)
                .accessibilityLabel("Next match")
                .help("Next match (↵)")
                .disabled(find.query.isEmpty)
            Button("Done") { find.close() }
                .meadowButton(small: true)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .background(meadow.paperRaised)
        .overlay(alignment: .bottom) { meadow.line.frame(height: 1) }
        .onChange(of: find.focusField, initial: true) { _, asked in
            guard asked else { return }
            focused = true
            find.focusField = false
        }
    }
}
#endif
