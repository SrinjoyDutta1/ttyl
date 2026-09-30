import AppKit
import Foundation

/// Runs `ttyl serve` and turns its JSON lines into published snapshots.
@MainActor
final class Engine: ObservableObject {
    @Published var snap: Snapshot?
    @Published var phase = true  // flips while something rings, so the phone flashes
    @Published var notice: String?
    @Published var problem: String?

    private var process: Process?
    private var input: FileHandle?
    private var buffer = Data()
    private var blink: Timer?
    private let demo: Bool

    init(demo: Bool = CommandLine.arguments.contains("--demo"), start: Bool = true) {
        self.demo = demo
        guard start else { return }
        Notifier.shared.setUp(engine: self)
        launch()
        if CommandLine.arguments.contains("--self-test-panel") { selfTestPanel() }
        blink = Timer.scheduledTimer(withTimeInterval: 0.45, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, (self.snap?.ringing ?? 0) > 0 else { return }
                self.phase.toggle()
            }
        }
    }

    /// For snapshots: a fixed state, no process.
    init(snapshot: Snapshot) {
        self.demo = true
        self.snap = snapshot
    }

    // MARK: talking to ttyl

    /// Where the `ttyl` engine lives: baked in at build time, else the usual install spots.
    static func ttylPath() -> String {
        let home = NSHomeDirectory()
        let baked = (Bundle.main.object(forInfoDictionaryKey: "TTYLCommand") as? String) ?? ""
        let candidates = [baked, "\(home)/.local/bin/ttyl", "/opt/homebrew/bin/ttyl", "/usr/local/bin/ttyl"]
        return candidates.first { !$0.isEmpty && FileManager.default.isExecutableFile(atPath: $0) } ?? "ttyl"
    }

    /// Run the engine directly (not through your shell, whose startup files may not expect
    /// to run without a terminal), with a PATH that has the tools it calls: ps, lsof, git, osascript.
    static func engineProcess(_ args: [String]) -> Process {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: ttylPath())
        p.arguments = args
        var env = ProcessInfo.processInfo.environment
        let home = NSHomeDirectory()
        env["PATH"] = ["\(home)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
                       "/usr/sbin", "/sbin", env["PATH"] ?? ""].joined(separator: ":")
        p.environment = env
        return p
    }

    private func launch() {
        let p = Engine.engineProcess(["serve"] + (demo ? ["--demo"] : []))
        let out = Pipe(), inp = Pipe()
        p.standardOutput = out
        p.standardInput = inp
        p.standardError = FileHandle.nullDevice
        out.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            Task { @MainActor in self?.consume(data) }
        }
        p.terminationHandler = { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.problem = "the ttyl engine stopped; restarting…"
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                self.launch()
            }
        }
        do {
            try p.run()
            process = p
            input = inp.fileHandleForWriting
        } catch {
            problem = "couldn't start ttyl: \(error.localizedDescription)"
        }
    }

    private func consume(_ data: Data) {
        buffer.append(data)
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer.subdata(in: buffer.startIndex..<nl)
            buffer.removeSubrange(buffer.startIndex...nl)
            guard let s = try? JSONDecoder.ttyl.decode(Snapshot.self, from: line) else { continue }
            snap = s
            problem = nil
            if let n = s.notices.last { flash(n) }
            for r in s.rings { Notifier.shared.post(r) }
            for a in s.alerts { Notifier.shared.post(a) }
        }
    }

    private func send(_ cmd: [String: Any]) {
        guard let input, let data = try? JSONSerialization.data(withJSONObject: cmd) else { return }
        input.write(data + Data([0x0A]))
    }

    func go(_ s: SessionInfo) {
        Log.write("clicked \(s.project) (\(s.where.isEmpty ? s.status : s.where))")
        go(id: s.id)
    }
    func go(id: String) { send(["cmd": "go", "id": id]) }
    func testRing() { send(["cmd": "test_ring"]) }
    func setSummaries(_ on: Bool) { send(["cmd": "summaries", "on": on]) }
    func archive(_ s: SessionInfo, _ on: Bool) { send(["cmd": on ? "archive" : "unarchive", "id": s.id]) }
    func trash(_ s: SessionInfo) { send(["cmd": "delete", "id": s.id]) }
    func stopRinging(_ s: SessionInfo) { send(["cmd": "ack", "id": s.id]) }
    func setShowAll(_ on: Bool) { send(["cmd": "all", "on": on]) }
    func openTerminalView() { send(["cmd": "open_terminal_view"]) }

    private func flash(_ text: String) {
        notice = text
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 6_000_000_000)
            if self.notice == text { self.notice = nil }
        }
    }

    /// `ttyl-bar --demo --self-test-panel`: open the panel, close it the way a row click does,
    /// report whether each worked, and exit. For checking the close trick without a mouse.
    private func selfTestPanel() {
        func after(_ secs: Double, _ step: @escaping @MainActor () -> Void) {
            DispatchQueue.main.asyncAfter(deadline: .now() + secs) { MainActor.assumeIsolated { step() } }
        }
        after(2.0) {
            print("status item found: \(Panel.statusButton != nil)")
            Panel.open()
        }
        after(3.5) {
            print("panel opened: \(Panel.window != nil)")
            Panel.close()
        }
        after(4.5) {
            print("panel closed: \(Panel.window == nil)")
            Panel.open()
        }
        after(6.0) {
            print("panel reopens after a programmatic close: \(Panel.window != nil)")
            Panel.close()
            self.quit()
        }
    }

    func quit() {
        process?.terminationHandler = nil
        process?.terminate()
        NSApp.terminate(nil)
    }
}
