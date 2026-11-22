#if os(macOS)
import SwiftUI
import UniformTypeIdentifiers

struct HostedCompose: View {
    @Environment(\.meadow) private var meadow
    @Environment(\.dismiss) private var dismiss
    let model: EncryptedMailboxModel
    @State private var attaching = false
    @State private var confirmingDiscard = false

    private var editing: Bool { model.hasDraft && model.draft.pending == nil && !model.sending }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Compose mail").font(.meadow(.display, 21, .bold)).foregroundStyle(meadow.ink)
            if model.hasDraft {
                Text("From  " + (model.address ?? "")).foregroundStyle(meadow.ink2)
                TextField("To", text: field(\.to)).disabled(!editing)
                TextField("Subject", text: field(\.subject)).disabled(!editing)
                TextEditor(text: field(\.body)).frame(minHeight: 160).border(.separator).disabled(!editing)
                    .accessibilityLabel("Message body")
                ForEach(model.draft.files) { file in
                    HStack {
                        Label(file.name, systemImage: "paperclip")
                        Spacer()
                        Button("Remove", systemImage: "xmark") {
                            var draft = model.draft
                            draft.files.removeAll { $0.id == file.id }
                            model.saveDraft(draft)
                        }.disabled(!editing)
                    }
                }
            }
            if let status = model.sendStatus { Text(status).font(.callout) }
            HStack {
                if model.hasDraft {
                    Button("Attach file…", systemImage: "paperclip") { attaching = true }.disabled(!editing)
                    if editing || model.failedSubmission {
                        Button("Discard draft…") { confirmingDiscard = true }
                    }
                }
                Spacer()
                Button(model.hasDraft ? "Close" : "Done") { dismiss() }.keyboardShortcut(.cancelAction)
                if model.draft.submission != nil {
                    Button("Check delivery") { Task { await model.checkSubmission() } }
                } else if model.hasDraft {
                    Button(model.draft.pending == nil ? "Send" : "Retry send") { Task { await model.send() } }
                        .keyboardShortcut(.defaultAction)
                        .disabled(model.sending || model.draft.to.isEmpty)
                }
            }
        }
        .padding(20).frame(minWidth: 480)
        .background(meadow.paper)
        .buttonStyle(MeadowButtonStyle())
        .confirmationDialog("Discard this draft?", isPresented: $confirmingDiscard, titleVisibility: .visible) {
            Button("Discard draft", role: .destructive) {
                model.discardDraft()
                if !model.hasDraft { dismiss() }
            }
        }
        .fileImporter(isPresented: $attaching, allowedContentTypes: [.data]) { result in
            guard let file = try? result.get() else { return }
            let scoped = file.startAccessingSecurityScopedResource()
            defer { if scoped { file.stopAccessingSecurityScopedResource() } }
            var draft = model.draft
            let room = 1_700_000 - draft.files.reduce(0) { $0 + $1.bytes.count }
            do {
                let values = try file.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
                guard values.isRegularFile == true, let size = values.fileSize, size <= room,
                      draft.files.count < 10 else { throw CocoaError(.fileReadTooLarge) }
                let bytes = try Data(contentsOf: file, options: .mappedIfSafe)
                guard bytes.count <= room else { throw CocoaError(.fileReadTooLarge) }
                draft.files.append(HostedDraftFile(id: UUID(), name: file.lastPathComponent, bytes: bytes))
                model.saveDraft(draft)
            } catch { model.sendStatus = "Choose a regular file within the 1.7 MB combined attachment limit." }
        }
    }

    private func field(_ keyPath: WritableKeyPath<HostedDraft, String>) -> Binding<String> {
        Binding(get: { model.draft[keyPath: keyPath] }, set: { value in
            guard editing else { return }
            var draft = model.draft
            draft[keyPath: keyPath] = value
            model.saveDraft(draft)
        })
    }
}
#endif
