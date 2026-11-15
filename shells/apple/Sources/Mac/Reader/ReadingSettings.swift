#if os(macOS)
import SwiftUI

/// Reading settings (Aa), as the page's `reader/ReadMenu.tsx` offers them: the column's width, plain text's face and
/// size, and mail's colours, each kept with the rest of the preferences on the server.
struct ReadingSettingsButton: View {
    @State private var shown = false

    var body: some View {
        Button {
            shown.toggle()
        } label: {
            Label("Reading Settings", systemImage: "textformat.size")
        }
        .help("Reading settings — column width, text and mail colors")
        .popover(isPresented: $shown, arrowEdge: .bottom) {
            ReadingSettings()
                .task { await Composing.shared.readPreferences() }
        }
    }
}

struct ReadingSettings: View {
    private var composing: Composing { .shared }

    var body: some View {
        let prefs = ReadingPrefs(composing.preferences)
        Form {
            Picker("Column width", selection: binding(prefs.width) { API.ChangedPreferences(readWidth: $0) }) {
                Text("Narrow").tag(API.ReadWidth.narrow)
                Text("Medium").tag(API.ReadWidth.medium)
                Text("Wide").tag(API.ReadWidth.wide)
            }
            Picker("Plain-text face", selection: binding(prefs.look.font) { API.ChangedPreferences(readFont: $0) }) {
                Text("Sans").tag(API.ReadFont.sans)
                Text("Serif").tag(API.ReadFont.serif)
                Text("Mono").tag(API.ReadFont.mono)
            }
            Picker("Plain-text size", selection: binding(prefs.look.size) { API.ChangedPreferences(readSize: $0) }) {
                Text("Small").tag(API.ReadSize.s)
                Text("Medium").tag(API.ReadSize.m)
                Text("Large").tag(API.ReadSize.l)
                Text("Extra large").tag(API.ReadSize.xl)
            }
            Picker("Mail colors", selection: binding(prefs.look.colors) { API.ChangedPreferences(mailColors: $0) }) {
                Text("Auto").tag(API.MailColors.auto)
                Text("Dark").tag(API.MailColors.dark)
                Text("Original").tag(API.MailColors.original)
            }
            Text("Face and size apply to plain-text mail; rich mail keeps the sender’s own type. Auto shows rich mail in its own colors and plain text in the app’s.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .pickerStyle(.segmented)
        .formStyle(.grouped)
        .frame(width: 380)
        .navigationTitle("Reading Settings")
    }

    private func binding<T: Equatable>(_ now: T, _ changed: @escaping (T) -> API.ChangedPreferences) -> Binding<T> {
        Binding(get: { now }, set: { value in
            guard value != now else { return }
            Task { await composing.change(changed(value)) }
        })
    }
}
#endif
