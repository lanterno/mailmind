#if os(macOS)
import SwiftUI

/// A message in a window of its own, as a double-click on a row opens one: the reader's document for that copy, on
/// the pane's paper under a bar for the traffic lights, in the light the person chose. It speaks the core the mail
/// window opened, the process's one (`MailApp.shared`), and never opens one of its own: a window that opens before
/// that core is open, or once it didn't open, closes.
struct MessageWindow: View {
    static let scene = "message"
    /// The bar the traffic lights stand on.
    static let barHeight: CGFloat = 40
    let copy: MailCopy?
    /// The process's one app, whose core the window speaks.
    var app = MailApp.shared
    @State private var reader = ReaderModel()
    @State private var find = ReaderFind()
    @Environment(\.dismiss) private var dismiss

    /// The core a message's window speaks: `app`'s once it is open, else none.
    static func core(of app: MailApp) -> Core? {
        app.stage == .open ? app.core : nil
    }

    var body: some View {
        if let copy, let core = Self.core(of: app) {
            MessageWindowContent(core: core, copy: copy, reader: reader, find: find)
                .task(id: copy) {
                    reader.core = core
                    reader.open(copy)
                }
        } else {
            Color.clear
                .frame(width: 1, height: 1)
                .onAppear { dismiss() }
        }
    }
}

private struct MessageWindowContent: View {
    let core: Core
    let copy: MailCopy
    let reader: ReaderModel
    let find: ReaderFind
    @Environment(\.meadow) private var meadow
    private var status: AppStatus { .shared }

    private var opened: API.MessageOpened? {
        if case .open(_, let opened) = reader.stage { return opened }
        return nil
    }

    var body: some View {
        VStack(spacing: 0) {
            Color.clear.frame(height: MessageWindow.barHeight)
            if find.open, opened != nil { ReaderFindBar(find: find) }
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .environment(\.readerFind, find)
        }
        .background { ZStack { meadow.paper; MeadowGrain() }.ignoresSafeArea() }
        .ignoresSafeArea()
        .meadowWindowChrome(barHeight: MessageWindow.barHeight)
        .navigationTitle(opened?.subject.flatMap { $0.isEmpty ? nil : $0 } ?? String(localized: "Message"))
        .focusedSceneValue(\.readerTools, opened.map { opened in
            ReaderTools(print: {
                MessagePrinter.shared.print(copy, opened: opened, core: core, prefs: ReadingPrefs(status.preferences),
                                            window: NSApp.keyWindow)
            }, find: { find.show() })
        })
    }

    @ViewBuilder private var content: some View {
        switch reader.stage {
        case .empty, .loading:
            Fetching()
        case .failed(_, let why):
            PaneNote(title: String(localized: "This message couldn’t be opened"), line: why)
        case .open(let copy, let opened):
            MessageReader(core: core, copy: copy, opened: opened, row: nil, prefs: ReadingPrefs(status.preferences),
                          mine: Composing.shared.mine)
        }
    }
}
#endif
