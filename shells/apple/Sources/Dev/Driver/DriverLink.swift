#if DEBUG && os(macOS)
import Foundation

/// The driver's end of the link: a Unix socket the runner listens on, which the app connects out to. It speaks JSON
/// lines, a command in (`{"id": 7, "do": "press", "keys": "e"}`) and its answer out (`{"id": 7, "ok": true, …}` or
/// `{"id": 7, "ok": false, "error": "…"}`), one at a time. Its own thread reads and writes; each command runs on the
/// main actor (`DriverCommands`). The runner going away ends the app: a driven app is never left running alone.
final class DriverLink: @unchecked Sendable {
    private let socket: Int32

    /// Connected to the runner at `path`, or nil.
    init?(path: String) {
        let made = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard made >= 0 else { return nil }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8)
        guard bytes.count < MemoryLayout.size(ofValue: address.sun_path) else {
            close(made)
            return nil
        }
        withUnsafeMutableBytes(of: &address.sun_path) { raw in
            raw.copyBytes(from: bytes)
            raw[bytes.count] = 0
        }
        let connected = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(made, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else {
            close(made)
            return nil
        }
        socket = made
    }

    /// Says hello, then answers each command until the runner hangs up.
    func start() {
        let thread = Thread { [self] in run() }
        thread.name = "mailmind.driver"
        thread.start()
    }

    private func run() {
        write(["hello": true, "pid": Int(getpid()), "lane": "A",
               "cannot_vouch_for": ["activation", "the real menu bar", "drag and drop"]])
        var pending = Data()
        var buffer = [UInt8](repeating: 0, count: 65_536)
        while true {
            let count = read(socket, &buffer, buffer.count)
            if count <= 0 { break }
            pending.append(contentsOf: buffer[0..<count])
            while let end = pending.firstIndex(of: 0x0A) {
                let line = pending[pending.startIndex..<end]
                pending.removeSubrange(pending.startIndex...end)
                answer(Data(line))
            }
        }
        // The runner is gone: so is the app.
        exit(0)
    }

    private func answer(_ line: Data) {
        guard let command = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] else {
            write(["ok": false, "error": "not a JSON object"])
            return
        }
        let id = command["id"] ?? NSNull()
        let given = Given(value: command)
        let done = DispatchSemaphore(value: 0)
        let reply = Given(value: [:])
        Task { @MainActor in
            reply.value = await DriverCommands.run(given.value)
            done.signal()
        }
        done.wait()
        var out = reply.value
        out["id"] = id
        write(out)
    }

    private func write(_ object: [String: Any]) {
        guard var data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else {
            write(["ok": false, "error": "the answer wasn't JSON"])
            return
        }
        data.append(0x0A)
        data.withUnsafeBytes { raw in
            var sent = 0
            while sent < raw.count {
                let wrote = Darwin.write(socket, raw.baseAddress! + sent, raw.count - sent)
                if wrote <= 0 { exit(0) }
                sent += wrote
            }
        }
    }

    /// A JSON object handed between the link's thread and the main actor, one at a time.
    private final class Given: @unchecked Sendable {
        var value: [String: Any]
        init(value: [String: Any]) { self.value = value }
    }
}
#endif
