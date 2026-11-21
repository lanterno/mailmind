#if os(macOS)
import AppKit
import QuickLook
import SwiftUI

/// A message's files, as the page lists them: each saved through a save panel, or shown with Quick Look. MailMind
/// never opens one itself, and what it writes to disk carries the quarantine a download does, so the system checks it
/// before anything runs it.
struct AttachmentsRow: View {
    let core: Core
    let copy: MailCopy
    let files: [API.AttachmentPart]
    @State private var looking: URL?
    @State private var busy: String?

    /// The files a person sees: a picture shown in the text is the text's.
    static func shown(_ parts: [API.AttachmentPart]) -> [API.AttachmentPart] {
        parts.filter { !($0.isInline != 0 && !($0.contentId ?? "").isEmpty) }
    }

    var body: some View {
        FlowLayout(spacing: 10) {
            ForEach(files, id: \.partId) { file in
                AttachmentCard(file: file, busy: busy == file.partId) {
                    Task { await look(file) }
                } save: {
                    Task { await save(file) }
                }
            }
        }
        .quickLookClosable($looking)
        .onChange(of: looking) { old, new in
            if new == nil, let old { Self.forget(old) }
        }
    }

    /// Never trust a filename a message gave: no path, and no control character, quote or line break (the server's
    /// `safe_filename`).
    static func safeName(_ name: String) -> String {
        let last = name.split(whereSeparator: { $0 == "/" || $0 == "\\" }).last.map(String.init) ?? ""
        let cleaned = String(last.unicodeScalars.map { $0.value < 0x20 || $0.value == 0x7f || $0 == "\"" ? "_" : Character($0) })
        let trimmed = cleaned.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty || trimmed.allSatisfy({ $0 == "." }) ? "attachment" : trimmed
    }

    private func fetch(_ file: API.AttachmentPart) async -> Data? {
        busy = file.partId
        defer { busy = nil }
        do {
            return try await core.document(API.Get.MessageAttachment.self, [
                "key": String(copy.key), "folder": copy.folder, "uid": String(copy.uid), "part_id": file.partId,
            ]).data
        } catch {
            Composing.shared.say("Couldn’t get \(file.filename): \(said(error))")
            return nil
        }
    }

    private func save(_ file: API.AttachmentPart) async {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = Self.safeName(file.filename)
        panel.canCreateDirectories = true
        let answer: NSApplication.ModalResponse
        if let window = NSApp.keyWindow {
            answer = await panel.beginSheetModal(for: window)
        } else {
            answer = panel.runModal()
        }
        guard answer == .OK, let url = panel.url, let data = await fetch(file) else { return }
        do {
            try data.write(to: url, options: .atomic)
            Self.quarantine(url)
            Composing.shared.say("Saved \(url.lastPathComponent)")
        } catch {
            Composing.shared.say("Couldn’t save \(file.filename)")
        }
    }

    /// The file where Quick Look's copies are kept (`PreviewFiles`: this user's alone, gone when its preview closes).
    private func look(_ file: API.AttachmentPart) async {
        guard let data = await fetch(file) else { return }
        do {
            let url = try PreviewFiles.write(data, named: Self.safeName(file.filename))
            Self.quarantine(url)
            looking = url
        } catch {
            Composing.shared.say("Couldn’t show \(file.filename)")
        }
    }

    private static func forget(_ url: URL) {
        PreviewFiles.remove(url)
    }

    /// Marked as a download from mail, so the system asks before it opens and checks before anything runs it.
    static func quarantine(_ url: URL) {
        var values = URLResourceValues()
        values.quarantineProperties = [
            kLSQuarantineTypeKey as String: kLSQuarantineTypeEmailAttachment as String,
            kLSQuarantineAgentNameKey as String: "MailMind",
        ]
        var marked = url
        try? marked.setResourceValues(values)
    }
}

/// What kind of file an attachment is, as its card's badge says it (`.att .ft`): its extension, short, or what its
/// type is when it has none.
enum FileKind {
    static func label(filename: String, contentType: String) -> String {
        let name = filename.split(whereSeparator: { $0 == "/" || $0 == "\\" }).last.map(String.init) ?? ""
        if let dot = name.lastIndex(of: "."), dot != name.startIndex {
            let ext = name[name.index(after: dot)...].uppercased()
            if (1...4).contains(ext.count), ext.allSatisfy({ $0.isLetter || $0.isNumber }) { return ext }
        }
        let type = contentType.lowercased()
        if type.hasPrefix("image/") { return "IMG" }
        if type.hasPrefix("text/") { return "TXT" }
        if type == "application/pdf" { return "PDF" }
        return "FILE"
    }
}

/// One file of a message (`.att`): its kind's badge, its name, its size, and Open (Quick Look) and Download (a save
/// panel). Clicking the card opens it too.
private struct AttachmentCard: View {
    let file: API.AttachmentPart
    let busy: Bool
    let open: () -> Void
    let save: () -> Void
    @Environment(\.meadow) private var meadow
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 10) {
            Text(FileKind.label(filename: file.filename, contentType: file.contentType))
                .font(.meadow(.ui, 9.5, .heavy))
                .tracking(0.57)
                .foregroundStyle(.white)
                .padding(.bottom, 5)
                .frame(width: 32, height: 38, alignment: .bottom)
                .background(LinearGradient(colors: [Color(meadow: 0xC8573C), Color(meadow: 0xA3412A)],
                                           startPoint: .topLeading, endPoint: .bottomTrailing),
                            in: RoundedRectangle(cornerRadius: 6))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: file.filename)
                    .font(.meadow(.ui, 13.5, .bold))
                    .foregroundStyle(meadow.ink)
                    .lineLimit(1)
                    .truncationMode(.middle)
                HStack(spacing: 0) {
                    Text(verbatim: FileSize.words(file.size) + " · ")
                        .monospacedDigit()
                    Button("Open", action: open)
                        .buttonStyle(AttachmentWordStyle())
                        .focusEffectDisabled()
                        .help("Quick Look \(file.filename)")
                    Text(verbatim: " · ")
                    Button("Download", action: save)
                        .buttonStyle(AttachmentWordStyle())
                        .focusEffectDisabled()
                        .help("Save \(file.filename)…")
                        .accessibilityLabel("Save \(file.filename)")
                    if busy {
                        ProgressView().controlSize(.mini).padding(.leading, 6)
                    }
                }
                .font(.meadow(.ui, 12))
                .foregroundStyle(meadow.ink3)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .frame(minWidth: 240, alignment: .leading)
        .fixedSize()
        .background(meadow.paperRaised, in: RoundedRectangle(cornerRadius: 12))
        .overlay {
            RoundedRectangle(cornerRadius: 12).strokeBorder(hovering ? meadow.line2 : meadow.line, lineWidth: 1)
        }
        .contentShape(RoundedRectangle(cornerRadius: 12))
        .onHover { hovering = $0 }
        .onTapGesture(perform: open)
        .contextMenu {
            Button("Quick Look", action: open)
            Button("Save…", action: save)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(file.filename), \(FileSize.words(file.size))")
    }
}

/// Open and Download in a file's card: its small words, underlined while the pointer is on them.
private struct AttachmentWordStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        AttachmentWord(label: configuration.label)
    }
}

private struct AttachmentWord: View {
    let label: ButtonStyleConfiguration.Label
    @Environment(\.meadow) private var meadow
    @Environment(\.isFocused) private var focused
    @State private var hovering = false

    var body: some View {
        label
            .underline(hovering)
            .foregroundStyle(hovering ? meadow.ink : meadow.ink3)
            .contentShape(Rectangle())
            .onHover { hovering = $0 }
            .meadowFocusRing(focused, radius: 4)
    }
}
#endif
