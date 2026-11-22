#if os(macOS)
import AppKit
import SwiftUI
import UniformTypeIdentifiers

private struct HostedAttachmentExport: FileDocument {
    static let readableContentTypes: [UTType] = [.data]
    let bytes: Data
    init(bytes: Data) { self.bytes = bytes }
    init(configuration: ReadConfiguration) throws { bytes = configuration.file.regularFileContents ?? Data() }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper { FileWrapper(regularFileWithContents: bytes) }
}

struct HostedMailboxScene: Scene {
    static let id = "hosted-mailbox"

    var body: some Scene {
        Window("Private mailbox", id: Self.id) {
            HostedMailboxView().meadowLit()
        }
        .defaultSize(width: 1080, height: 700)
        .restorationBehavior(.disabled)
        .commandsRemoved()
    }
}

struct HostedMailboxSettingsLink: View {
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button { openWindow(id: HostedMailboxScene.id) } label: {
            MeadowPaletteRow(String(localized: "Private mailbox…"), icon: .lock)
        }
        .buttonStyle(.plain)
        .help("Open a mailbox with a private profile")
    }
}

struct HostedMailboxView: View {
    @Environment(\.meadow) private var meadow
    @State private var model = EncryptedMailboxModel()
    @State private var composing = false
    @State private var attachmentExporter = false
    @State private var attachmentDocument: HostedAttachmentExport?
    @State private var attachmentName = ""

    var body: some View {
        VStack(spacing: 10) {
            banner
            if model.ready {
                HStack(spacing: 10) {
                    folderRail.frame(width: 155)
                    messageList.frame(minWidth: 210, idealWidth: 270, maxWidth: 310)
                    reader.frame(maxWidth: .infinity)
                }
            } else {
                empty("Connect a mailbox", icon: .lock) {
                    Button("Import mailbox profile…", action: chooseProfile)
                        .buttonStyle(MeadowButtonStyle(kind: .primary))
                }
                .meadowPanel()
            }
            if let issue = model.status {
                HStack {
                    MeadowIconView(.alert, size: 15)
                    Text(issue).font(.meadow(.ui, 12))
                    Spacer()
                }
                .foregroundStyle(meadow.ink2)
                .padding(.horizontal, 8)
            }
        }
        .padding(12)
        .frame(minWidth: 680, minHeight: 420)
        .background { MeadowSky().ignoresSafeArea() }
        .sheet(isPresented: $composing) { HostedCompose(model: model) }
        .fileExporter(isPresented: $attachmentExporter, document: attachmentDocument, contentType: .data,
                      defaultFilename: attachmentName) { result in
            if case .failure = result { model.status = "The attachment could not be saved." }
            attachmentDocument = nil
        }
        .onChange(of: model.folder) { model.selected = nil }
        .onChange(of: model.selected) { model.openSelected() }
        .task {
            await model.start()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(30)) } catch { break }
                await model.refresh()
            }
        }
    }

    private func chooseProfile() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.prompt = String(localized: "Import")
        let take = { (answer: NSApplication.ModalResponse) in
            guard answer == .OK, let url = panel.url else { return }
            Task { await model.importProfile(url) }
        }
        if let window = NSApp.keyWindow {
            panel.beginSheetModal(for: window, completionHandler: take)
        } else {
            take(panel.runModal())
        }
    }

    private var banner: some View {
        HStack(spacing: 10) {
            MeadowBrandMark(size: 29)
            VStack(alignment: .leading, spacing: 2) {
                Text("Private mailbox")
                    .font(.meadow(.display, 19, .bold))
                    .foregroundStyle(meadow.ink)
                if let address = model.address {
                    Text(address).font(.meadow(.ui, 12)).foregroundStyle(meadow.ink2)
                }
            }
            Spacer()
            if model.syncing { ProgressView().controlSize(.small).accessibilityLabel("Syncing mailbox") }
            if model.ready {
                action("Refresh", icon: .refresh) { Task { await model.refresh() } }
                    .disabled(model.syncing)
                action(model.hasDraft ? "Open draft" : "Compose", icon: .pen, primary: true) {
                    if !model.hasDraft {
                        model.sendStatus = nil
                        model.saveDraft(HostedDraft())
                    }
                    composing = true
                }
                .disabled(model.address == nil)
            }
        }
        .padding(.horizontal, 6)
    }

    private var folderRail: some View {
        List(model.folders, selection: $model.folder) { folder in
            HStack(spacing: 8) {
                MeadowIconView(folderIcon(folder.role), size: 16)
                Text(folder.name).font(.meadow(.ui, 13, .bold))
            }
            .tag(folder.id)
        }
        .scrollContentBackground(.hidden)
        .meadowPanel(opacity: 0.86)
        .accessibilityLabel("Mailbox folders")
    }

    private var messageList: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(model.folders.first { $0.id == model.folder }?.name ?? String(localized: "Messages"))
                .font(.meadow(.display, 20, .bold))
                .foregroundStyle(meadow.ink)
                .padding(13)
            List(selection: $model.selected) {
                ForEach(model.items, id: \.0.id) { entry, header in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 6) {
                            if !entry.read {
                                Circle().fill(meadow.primary).frame(width: 6, height: 6).accessibilityLabel("Unread")
                            }
                            Text(header.subject.isEmpty ? String(localized: "(No subject)") : header.subject)
                                .font(.meadow(.ui, 13, entry.read ? .regular : .bold)).lineLimit(1)
                        }
                        Text(header.sender).font(.meadow(.ui, 12)).lineLimit(1)
                    }
                    .padding(.vertical, 4)
                    .tag(entry.id)
                }
            }
            .scrollContentBackground(.hidden)
            .overlay {
                if model.items.isEmpty, !model.syncing {
                    empty("No messages", icon: .inbox) { EmptyView() }
                }
            }
        }
        .meadowPanel()
        .accessibilityLabel("Message list")
    }

    private var reader: some View {
        Group {
            if let header = model.currentHeader, let reading = model.reading {
                VStack(alignment: .leading, spacing: 0) {
                    if let entry = model.currentEntry {
                        HStack {
                            action(entry.read ? "Mark unread" : "Mark read", icon: entry.read ? .mail : .mailOpen) {
                                Task { await model.changeSelected(read: !entry.read) }
                            }
                            if let destination = model.destination {
                                action(destination.role == "archive" ? "Archive" : "Move to Inbox",
                                       icon: destination.role == "archive" ? .archive : .inbox) {
                                    Task { await model.changeSelected(folder: destination.id) }
                                }
                            }
                            action("Reply", icon: .reply) {
                                if !model.hasDraft { model.prepareReply() }
                                composing = true
                            }
                        }
                        .padding(12)
                        Divider()
                    }
                    ScrollView {
                        VStack(alignment: .leading, spacing: 14) {
                            Text(header.subject.isEmpty ? String(localized: "(No subject)") : header.subject)
                                .font(.meadow(.display, 25, .bold))
                            Text(header.sender).font(.meadow(.ui, 14, .bold)).textSelection(.enabled)
                            if let timestamp = model.currentEntry?.received {
                                Text(Date(timeIntervalSince1970: Double(timestamp) / 1000), format: .dateTime)
                                    .font(.meadow(.ui, 12)).foregroundStyle(meadow.ink2)
                            }
                            Divider()
                            Text(reading.text.isEmpty ? String(localized: "This message has no readable text.") : reading.text)
                                .font(.meadow(.ui, 14))
                                .textSelection(.enabled)
                            ForEach(reading.attachments, id: \.partId) { part in
                                action(part.filename, icon: .download) {
                                    if let bytes = model.attachment(part) {
                                        attachmentDocument = HostedAttachmentExport(bytes: bytes)
                                        attachmentName = part.filename
                                        attachmentExporter = true
                                    }
                                }
                                .help("Save attachment")
                            }
                        }
                        .foregroundStyle(meadow.ink)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(20)
                    }
                }
            } else {
                empty("Select a message", icon: .mail) { EmptyView() }
            }
        }
        .meadowPanel()
        .accessibilityLabel("Reading pane")
    }

    private func empty<Trailing: View>(_ title: String, icon: MeadowIcon, @ViewBuilder trailing: () -> Trailing) -> some View {
        VStack(spacing: 12) {
            MeadowIconView(icon, size: 26)
            Text(title).font(.meadow(.display, 18, .bold))
            trailing()
        }
        .foregroundStyle(meadow.ink2)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func action(_ title: String, icon: MeadowIcon, primary: Bool = false, run: @escaping () -> Void) -> some View {
        Button(action: run) {
            HStack(spacing: 5) {
                MeadowIconView(icon, size: 15)
                Text(title).lineLimit(1)
            }
        }
        .buttonStyle(MeadowButtonStyle(kind: primary ? .primary : .standard, small: true))
        .help(title)
    }

    private func folderIcon(_ role: String?) -> MeadowIcon {
        switch role {
        case "inbox": .inbox
        case "sent": .send
        case "archive": .archive
        default: .folder
        }
    }
}
#endif
