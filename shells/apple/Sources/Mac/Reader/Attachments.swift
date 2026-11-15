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
        FlowLayout(spacing: 6) {
            ForEach(files, id: \.partId) { file in
                chip(file)
            }
        }
        .quickLookPreview($looking)
        .onChange(of: looking) { old, new in
            if new == nil, let old { Self.forget(old) }
        }
    }

    private func chip(_ file: API.AttachmentPart) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "paperclip")
                .foregroundStyle(.secondary)
            Text(file.filename)
                .lineLimit(1)
                .truncationMode(.middle)
            Text(FileSize.words(file.size))
                .foregroundStyle(.secondary)
                .monospacedDigit()
            if busy == file.partId {
                ProgressView().controlSize(.mini)
            }
            Button {
                Task { await save(file) }
            } label: {
                Image(systemName: "arrow.down.circle")
            }
            .buttonStyle(.borderless)
            .help("Save \(file.filename)…")
            .accessibilityLabel("Save \(file.filename)")
        }
        .font(.callout)
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(.quaternary.opacity(0.6), in: Capsule())
        .contentShape(Capsule())
        .onTapGesture { Task { await look(file) } }
        .contextMenu {
            Button("Quick Look") { Task { await look(file) } }
            Button("Save…") { Task { await save(file) } }
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint("Shows it with Quick Look")
        .help("Quick Look \(file.filename)")
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
#endif
