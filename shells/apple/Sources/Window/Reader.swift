import Foundation
import Observation

/// The message the reader shows: its headers from the route the page opens a body with (`/api/message/body`), and the
/// URL of its frame's document (`/api/message/body.html`), which the frame loads through the core on its own.
@MainActor
@Observable
final class ReaderModel {
    enum Stage: Equatable {
        case empty
        case loading(MailCopy)
        case open(MailCopy, API.MessageOpened)
        case failed(MailCopy, String)
    }

    private(set) var stage = Stage.empty
    @ObservationIgnored var core: Core?
    @ObservationIgnored private var opening: Task<Void, Never>?

    /// Open `copy`, or show nothing.
    func open(_ copy: MailCopy?) {
        opening?.cancel()
        guard let copy, let core else {
            stage = .empty
            return
        }
        stage = .loading(copy)
        opening = Task { [weak self] in
            let query: KeyValuePairs<String, String?> = ["key": String(copy.key), "folder": copy.folder, "uid": String(copy.uid)]
            let read: Stage
            do {
                switch try await core.get(API.Get.MessageBody.self, query) {
                case .messageOpened(let opened): read = .open(copy, opened)
                case .prefetched: read = .failed(copy, String(localized: "This message couldn't be opened."))
                }
            } catch {
                read = .failed(copy, (error as? Problem)?.description ?? String(localized: "This message couldn't be opened."))
            }
            guard !Task.isCancelled else { return }
            self?.stage = read
        }
    }

    /// The frame's document for `copy`, as the page's `bodyUrl` names it, under the scheme the frame's handler answers.
    static func frameURL(_ copy: MailCopy, images: Bool = false) -> URL? {
        let query = Core.query(["key": String(copy.key), "folder": copy.folder, "uid": String(copy.uid),
                                "images": images ? "1" : "0"])
        return URL(string: "\(FrameScheme.scheme)://mail\(FrameScheme.path)\(query)")
    }
}

/// What the reader's frame may load through the core: the mail body's own document and nothing else, so even a body
/// that got past every other wall reaches no other route.
enum FrameScheme {
    static let scheme = "mailmind-frame"
    static let path = "/api/message/body.html"

    /// The route's target for a URL the frame asks for, or nil for any URL that isn't a mail body's document.
    static func target(of url: URL) -> String? {
        guard url.scheme == scheme, url.host() == "mail", url.path(percentEncoded: true) == path else { return nil }
        return path + (url.query(percentEncoded: true).map { "?" + $0 } ?? "")
    }
}
