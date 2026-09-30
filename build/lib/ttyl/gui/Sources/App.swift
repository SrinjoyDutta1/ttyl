import AppKit
import SwiftUI

struct TtylBar: App {
    @StateObject private var engine = Engine()

    var body: some Scene {
        MenuBarExtra {
            PanelView().environmentObject(engine)
        } label: {
            MenuLabel(engine: engine)
        }
        .menuBarExtraStyle(.window)
    }
}

@main
enum Main {
    static func main() {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count {
            MainActor.assumeIsolated { Snapshotter.render(to: args[i + 1]) }
            return
        }
        if let i = args.firstIndex(of: "--snapshot-live"), i + 1 < args.count {
            MainActor.assumeIsolated { Snapshotter.renderLive(to: args[i + 1]) }
            return
        }
        TtylBar.main()
    }
}

/// `ttyl-bar --snapshot out.png`: draw the panel on demo data, for docs and checks.
@MainActor
enum Snapshotter {
    static func render(to path: String) {
        let p = Engine.engineProcess(["serve", "--demo", "--once", "--quiet"])
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { fail("couldn't run ttyl: \(error)") }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        let line = data.split(separator: 0x0A).last ?? Data()
        guard let snap = try? JSONDecoder.ttyl.decode(Snapshot.self, from: Data(line)) else { fail("no snapshot from ttyl") }

        let engine = Engine(snapshot: snap)
        let view = VStack(spacing: 0) {
            MenuBarStrip(engine: engine)
            PanelView(scrolls: false)
                .environmentObject(engine)
                .background(Color(red: 0.13, green: 0.13, blue: 0.14))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.white.opacity(0.12)))
                .padding(.horizontal, 10).padding(.bottom, 12)
        }
        .frame(width: 520)
        .background(Color(red: 0.08, green: 0.08, blue: 0.09))
        .environment(\.colorScheme, .dark)

        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        guard let image = renderer.nsImage, let tiff = image.tiffRepresentation,
              let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) else {
            fail("render failed")
        }
        do { try png.write(to: URL(fileURLWithPath: path)) } catch { fail("write failed: \(error)") }
        print(path)
    }

    /// The real panel (scroll view and all) laid out in an offscreen window, the way the
    /// menu bar shows it. Catches layout bugs the ImageRenderer snapshot can't see.
    static func renderLive(to path: String) {
        _ = NSApplication.shared
        let engine = Engine(snapshot: demoSnapshot())
        let host = NSHostingView(rootView: PanelView(scrolls: true).environmentObject(engine)
            .background(Color(red: 0.13, green: 0.13, blue: 0.14)).environment(\.colorScheme, .dark))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 500, height: 900), styleMask: [.borderless],
                              backing: .buffered, defer: false)
        window.contentView = host
        for _ in 0..<6 {  // let the height preference settle
            host.layoutSubtreeIfNeeded()
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        }
        let size = host.fittingSize
        window.setContentSize(size)
        host.layoutSubtreeIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))
        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { fail("no bitmap") }
        host.cacheDisplay(in: host.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { fail("no png") }
        do { try png.write(to: URL(fileURLWithPath: path)) } catch { fail("write failed: \(error)") }
        print("\(path) \(Int(size.width))x\(Int(size.height))")
    }

    static func demoSnapshot() -> Snapshot {
        let p = Engine.engineProcess(["serve", "--demo", "--once", "--quiet"])
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { fail("couldn't run ttyl: \(error)") }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        let line = data.split(separator: 0x0A).last ?? Data()
        guard let snap = try? JSONDecoder.ttyl.decode(Snapshot.self, from: Data(line)) else { fail("no snapshot from ttyl") }
        return snap
    }

    static func fail(_ msg: String) -> Never {
        FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
        exit(1)
    }
}

/// A stand-in for the macOS menu bar in snapshots, showing the ringing label.
struct MenuBarStrip: View {
    @ObservedObject var engine: Engine

    var body: some View {
        HStack(spacing: 16) {
            Spacer()
            MenuLabel(engine: engine)
                .padding(.horizontal, 8).padding(.vertical, 2)
                .background(RoundedRectangle(cornerRadius: 5).fill(Color.white.opacity(0.18)))
            Image(systemName: "wifi")
            Image(systemName: "battery.75percent")
            Text("Tue 2:41 PM")
        }
        .font(.system(size: 13, weight: .medium))
        .foregroundStyle(.white)
        .padding(.horizontal, 14).frame(height: 26)
        .background(Color.black.opacity(0.55))
        .padding(.bottom, 6)
    }
}
