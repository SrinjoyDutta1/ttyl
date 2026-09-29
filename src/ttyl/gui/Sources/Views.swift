import AppKit
import SwiftUI

// Glance, pick, go: grouped by what each session needs from you; click a row to go to it.

enum Palette {
    static let ring = Color(red: 0.93, green: 0.25, blue: 0.55)
    static let edit = Color(red: 0.30, green: 0.80, blue: 0.35)
    static let run = Color(red: 0.42, green: 0.58, blue: 0.80)
    static let commit = Color(red: 0.98, green: 0.62, blue: 0.20)
    static let fail = Color(red: 0.95, green: 0.30, blue: 0.35)
    static let active = Color(red: 0.25, green: 0.80, blue: 0.90)

    static func section(_ key: String) -> Color {
        switch key {
        case "needs": return ring
        case "working": return active
        case "finished": return edit
        default: return .secondary
        }
    }
}

// MARK: menu bar

struct MenuLabel: View {
    @ObservedObject var engine: Engine

    var body: some View {
        let ringing = engine.snap?.ringing ?? 0
        let needs = engine.snap?.needsYou ?? 0
        if ringing > 0 {
            Text(engine.phase ? "☎ ring ring" : "☏ ring ring")
        } else if needs > 0 {
            Text("☎ \(needs)")
        } else {
            Image(systemName: "phone")
        }
    }
}

// MARK: panel

/// Reports the height of the session list, so the scroll view can be exactly that tall.
/// (In a menu bar window a ScrollView has no natural height and collapses to nothing.)
struct ListHeight: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

struct PanelView: View {
    @EnvironmentObject var engine: Engine
    var scrolls = true  // off for snapshots: ImageRenderer can't draw scroll views
    @State private var listHeight: CGFloat = 0

    private var maxListHeight: CGFloat {
        min(640, (NSScreen.main?.visibleFrame.height ?? 800) - 160)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Header()
            if let n = engine.notice {
                Text(n)
                    .font(.system(size: 12, weight: .medium))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14).padding(.vertical, 7)
                    .background(n.hasPrefix("couldn't") ? Palette.fail.opacity(0.25) : Color.accentColor.opacity(0.22))
            }
            Divider()
            if let snap = engine.snap {
                if snap.sessions.isEmpty {
                    Text("No agent sessions. Start claude or codex in a terminal.")
                        .foregroundStyle(.secondary).padding(20)
                } else if scrolls {
                    ScrollView {
                        SessionList(snap: snap).background(
                            GeometryReader { g in Color.clear.preference(key: ListHeight.self, value: g.size.height) })
                    }
                    .frame(height: min(max(listHeight, 80), maxListHeight))
                    .onPreferenceChange(ListHeight.self) { listHeight = $0 }
                } else {
                    SessionList(snap: snap)
                }
            } else {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(engine.problem ?? "starting ttyl…").foregroundStyle(.secondary)
                }.padding(20)
            }
            Divider()
            Footer(interactive: scrolls)
        }
        .frame(width: 500)
    }
}

struct Header: View {
    @EnvironmentObject var engine: Engine

    var body: some View {
        HStack(spacing: 10) {
            Text("ttyl").font(.system(size: 15, weight: .bold, design: .rounded))
            if let snap = engine.snap {
                if snap.ringing > 0 {
                    Text(engine.phase ? "☎ ring ring" : "☏ ring ring")
                        .font(.system(size: 11, weight: .bold))
                        .padding(.horizontal, 7).padding(.vertical, 2)
                        .background(Capsule().fill(Palette.ring))
                        .foregroundStyle(.white)
                }
                Spacer()
                ForEach(snap.sections) { sec in
                    Text("\(sec.count) \(sec.title.lowercased())")
                        .font(.system(size: 11, weight: sec.key == "needs" ? .semibold : .regular))
                        .foregroundStyle(Palette.section(sec.key))
                }
            } else {
                Spacer()
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
    }
}

struct SessionList: View {
    let snap: Snapshot

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(snap.sections) { sec in
                SectionHeader(section: sec)
                ForEach(snap.members(of: sec)) { s in Row(s: s) }
            }
        }
        .padding(.horizontal, 8).padding(.bottom, 8)
    }
}

struct SectionHeader: View {
    let section: SectionInfo

    var body: some View {
        HStack(spacing: 6) {
            Text(section.title).font(.system(size: 10, weight: .bold)).foregroundStyle(Palette.section(section.key))
            Text("\(section.count)").font(.system(size: 10)).foregroundStyle(.secondary)
            if !section.hint.isEmpty {
                Text(section.hint).font(.system(size: 10)).foregroundStyle(.tertiary)
            }
            Spacer()
        }
        .padding(.horizontal, 6).padding(.top, 10).padding(.bottom, 2)
    }
}

struct Row: View {
    let s: SessionInfo
    @EnvironmentObject var engine: Engine
    @State private var hover = false
    @State private var confirmTrash = false

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Badge(s: s)
                VStack(alignment: .leading, spacing: 1) {
                    Text(s.title).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                        .foregroundStyle(s.isClosed ? .secondary : .primary)
                    Text(s.place).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 8)
                if hover {
                    Button { engine.archive(s, !s.archived) } label: {
                        Image(systemName: s.archived ? "tray.and.arrow.up" : "archivebox")
                    }
                    .buttonStyle(.borderless).foregroundStyle(.secondary)
                    .help(s.archived ? "Unarchive" : "Archive: hide it until it does something new")
                }
                VStack(alignment: .trailing, spacing: 1) {
                    Text(s.where.isEmpty ? s.when : s.where).font(.system(size: 11, design: .monospaced))
                    if !s.where.isEmpty { Text(s.when).font(.system(size: 10)).foregroundStyle(.tertiary) }
                }.foregroundStyle(.secondary)
            }
            if s.isRinging || !s.action.isEmpty {
                HStack(spacing: 6) {
                    if s.isRinging {
                        Text(engine.phase ? "☎ ring ring" : "☏ ring ring")
                            .font(.system(size: 11, weight: .bold)).foregroundStyle(Palette.ring)
                    }
                    Text(s.action.isEmpty ? s.lastReply : s.action)
                        .font(.system(size: 11, design: s.action.isEmpty ? .default : .monospaced))
                        .foregroundStyle(s.status == "waiting" ? Palette.ring : s.status == "busy" ? Palette.active : .secondary)
                        .lineLimit(1)
                }
                .padding(.leading, 30)
            }
            Squares(turns: s.turns, hidden: s.hiddenTurns).padding(.leading, 30)
            if hover && !s.about.isEmpty {
                Text(s.about).font(.system(size: 11)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 30)
            }
        }
        .padding(.horizontal, 8).padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(s.isRinging ? Palette.ring.opacity(engine.phase ? 0.20 : 0.10)
                      : hover ? Color.primary.opacity(0.07) : Color.clear)
        )
        .contentShape(Rectangle())
        .onHover { hover = $0 }
        .onTapGesture { open() }
        .contextMenu {
            Button(s.isClosed ? "Reopen" : "Go to terminal") { open() }
            Button(s.archived ? "Unarchive" : "Archive") { engine.archive(s, !s.archived) }
            if !s.resumeCommand.isEmpty {
                Button("Copy resume command") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(s.resumeCommand, forType: .string)
                }
            }
            if s.deletable {
                Divider()
                Button("Move to Trash…", role: .destructive) { confirmTrash = true }
            }
        }
        .confirmationDialog("Move “\(s.title)” to the Trash?", isPresented: $confirmTrash) {
            Button("Move to Trash", role: .destructive) { engine.trash(s) }
        } message: {
            Text("You won't be able to resume it. It goes to your Trash, so you can still put it back.")
        }
        .help(s.isClosed ? "Click to reopen: \(s.resumeCommand)" : "Click to go to \(s.where.isEmpty ? "it" : s.where)")
    }
}

extension Row {
    /// Go there, and get the panel out of the way so the terminal is the only thing you see.
    func open() {
        engine.go(s)
        if s.hasDestination { Panel.close() }
    }
}

/// The MenuBarExtra panel. SwiftUI can't close it from code, so we click its status item
/// (which keeps SwiftUI's idea of "open" in sync), the same trick MenuBarExtraAccess uses.
@MainActor
enum Panel {
    static var window: NSWindow? {
        NSApp.windows.first { $0.isVisible && $0.className.contains("MenuBarExtra") }
    }

    static var statusButton: NSStatusBarButton? {
        for w in NSApp.windows where w.className.contains("NSStatusBarWindow") {
            if w.responds(to: NSSelectorFromString("statusItem")),
               let item = w.value(forKey: "statusItem") as? NSStatusItem {
                return item.button
            }
        }
        return nil
    }

    static func close() {
        guard let panel = window else { return }
        if let button = statusButton {
            button.performClick(nil)
        } else {
            panel.close()
        }
    }

    static func open() {
        if window == nil { statusButton?.performClick(nil) }
    }
}

struct Badge: View {
    let s: SessionInfo
    @EnvironmentObject var engine: Engine

    var body: some View {
        ZStack {
            if s.isRinging {
                Circle().fill(Palette.ring)
                Image(systemName: "phone.fill").font(.system(size: 10, weight: .bold)).foregroundStyle(.white)
                    .rotationEffect(.degrees(engine.phase ? -14 : 14))
            } else {
                Circle().stroke(Color.secondary.opacity(0.4), lineWidth: 1)
                Text(s.number.map(String.init) ?? "·").font(.system(size: 11, weight: .semibold, design: .rounded))
                    .foregroundStyle(s.isClosed ? .secondary : .primary)
            }
        }
        .frame(width: 22, height: 22)
    }
}

/// One square per turn, oldest to newest. Hover a square to see that turn.
struct Squares: View {
    let turns: [TurnInfo]
    let hidden: Int

    var body: some View {
        HStack(spacing: 4) {
            if hidden > 0 {
                Text("+\(hidden)").font(.system(size: 10, design: .monospaced)).foregroundStyle(.tertiary)
            }
            ForEach(Array(turns.enumerated()), id: \.offset) { _, t in
                Square(t: t).help(t.tooltip)
            }
        }
    }
}

struct Square: View {
    let t: TurnInfo

    var body: some View {
        Group {
            switch t.kind {
            case "edit": RoundedRectangle(cornerRadius: 2.5).fill(Palette.edit)
            case "run": RoundedRectangle(cornerRadius: 2.5).fill(Palette.run)
            case "commit": RoundedRectangle(cornerRadius: 1.5).fill(Palette.commit).rotationEffect(.degrees(45)).scaleEffect(0.78)
            case "fail": Image(systemName: "xmark").font(.system(size: 9, weight: .heavy)).foregroundStyle(Palette.fail)
            case "active": Image(systemName: "play.fill").font(.system(size: 9)).foregroundStyle(Palette.active)
            case "waiting": RoundedRectangle(cornerRadius: 2.5).fill(Palette.ring)
                .overlay(RoundedRectangle(cornerRadius: 2.5).stroke(Color.white.opacity(0.8), lineWidth: 1.5).padding(2.5))
            default: RoundedRectangle(cornerRadius: 2.5).stroke(Color.secondary.opacity(0.7), lineWidth: 1.2)
            }
        }
        .frame(width: 11, height: 11)
    }
}

struct Footer: View {
    @EnvironmentObject var engine: Engine
    var interactive = true  // menus can't be drawn into snapshots

    var body: some View {
        HStack(spacing: 12) {
            Legend()
            Spacer()
            if !interactive {
                Image(systemName: "ellipsis.circle").foregroundStyle(.secondary)
            } else {
            Menu {
                Button("Open terminal view") { engine.openTerminalView() }
                Button("Test ring") { engine.testRing() }
                Toggle("Show all history", isOn: Binding(
                    get: { engine.snap?.showAll ?? false }, set: { engine.setShowAll($0) }))
                Divider()
                Button("Quit ttyl") { engine.quit() }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton).fixedSize()
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 8)
    }
}

struct Legend: View {
    var body: some View {
        HStack(spacing: 8) {
            item("edit", "edit"); item("run", "tools"); item("chat", "chat")
            item("commit", "commit"); item("fail", "fail"); item("waiting", "you")
        }
    }

    private func item(_ kind: String, _ label: String) -> some View {
        HStack(spacing: 3) {
            Square(t: TurnInfo(kind: kind, time: "", prompt: "", files: 0, commits: []))
            Text(label).font(.system(size: 10)).foregroundStyle(.secondary)
        }
    }
}
