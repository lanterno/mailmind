#if os(macOS)
import AppKit
import Network

/// What the core hears of the Mac's life (`CoreHost.woke`): a wake from sleep, and a network that really changed. After
/// either, a connection waiting in IDLE may be dead without a word and mail may have come while nothing listened, so
/// the core opens its connections afresh and syncs now rather than at its next round.
///
/// A burst (a wake, then the Wi-Fi coming back, then its address) is told once, a moment after its first.
@MainActor
final class Waking {
    /// A network path as far as a connection cares: whether it reaches anywhere, over which interfaces, through which
    /// gateways. A new address on the same Wi-Fi, which the system also reports, isn't part of it.
    struct Shape: Equatable, Sendable {
        var reaches: Bool
        var interfaces: [String]
        var gateways: [String]

        init(reaches: Bool, interfaces: [String] = [], gateways: [String] = []) {
            self.reaches = reaches
            self.interfaces = interfaces.sorted()
            self.gateways = gateways.sorted()
        }

        init(_ path: NWPath) {
            self.init(reaches: path.status == .satisfied,
                      interfaces: path.availableInterfaces.map { "\($0.type) \($0.name)" },
                      gateways: path.gateways.map { "\($0)" })
        }
    }

    /// The paths heard so far, and whether each is one the core must be told of.
    struct Paths: Equatable, Sendable {
        private(set) var last: Shape?

        /// Whether `now` is a change to tell: a path that reaches somewhere and isn't the one before. The first path
        /// heard is how things already stood when the core opened; a path that reaches nowhere waits for the next.
        mutating func heard(_ now: Shape) -> Bool {
            defer { last = now }
            guard let last else { return false }
            return now != last && now.reaches
        }
    }

    /// How long a burst is gathered before the core hears it.
    static let gather = Duration.seconds(2)

    private let woke: @MainActor () -> Void
    private let delay: Duration
    private var paths = Paths()
    private var soon: Task<Void, Never>?
    private var monitor: NWPathMonitor?
    private var observer: NSObjectProtocol?

    /// `woke` is the core's call; `delay` how long a burst is gathered.
    init(delay: Duration = Waking.gather, woke: @escaping @MainActor () -> Void) {
        self.delay = delay
        self.woke = woke
    }

    /// Listen for a wake and for the network, until `stop`.
    func start() {
        guard monitor == nil else { return }
        observer = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.tell() }
        }
        let monitor = NWPathMonitor()
        monitor.pathUpdateHandler = { [weak self] path in
            let shape = Shape(path)
            Task { @MainActor in self?.heard(shape) }
        }
        monitor.start(queue: DispatchQueue(label: "mailmind.network", qos: .utility))
        self.monitor = monitor
    }

    func stop() {
        monitor?.cancel()
        monitor = nil
        if let observer { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
        observer = nil
        soon?.cancel()
        soon = nil
    }

    /// A path the system reported: told when it is one the core must hear of.
    func heard(_ shape: Shape) {
        if paths.heard(shape) { tell() }
    }

    /// Tell the core once the burst this starts or joins is over.
    func tell() {
        guard soon == nil else { return }
        soon = Task { [weak self, delay] in
            try? await AppSeams.clock.sleep(for: delay)
            guard !Task.isCancelled, let self else { return }
            self.soon = nil
            self.woke()
        }
    }
}
#endif
