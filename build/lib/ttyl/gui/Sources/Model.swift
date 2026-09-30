import Foundation

// Mirrors the JSON that `ttyl serve` prints (src/ttyl/serve.py).

struct Snapshot: Decodable {
    let sections: [SectionInfo]
    let sessions: [SessionInfo]
    let ringing: Int
    let needsYou: Int
    let showAll: Bool
    let notices: [String]
    let rings: [RingEvent]  // sessions that started ringing since the last snapshot
    let alerts: [AlertEvent]  // new collisions since the last snapshot
    let collisions: Int  // files being edited by two sessions at once
    let summaries: SummariesInfo

    func members(of section: SectionInfo) -> [SessionInfo] {
        sessions.filter { $0.section == section.key }
    }
}

struct RingEvent: Decodable {
    let id: String
    let project: String
    let title: String
    let reason: String  // "needs you" | "finished"
    let what: String
}

struct SummariesInfo: Decodable {
    let on: Bool
    let note: String
}

struct AlertEvent: Decodable {
    let id: String  // the session a click on the notification goes to
    let title: String
    let text: String
}

struct CollisionInfo: Decodable {
    let path: String
    let with: [String]
}

struct SectionInfo: Decodable, Identifiable {
    let key: String
    let title: String
    let hint: String
    let count: Int
    var id: String { key }
}

struct TurnInfo: Decodable {
    let kind: String  // edit | run | chat | commit | fail | active | waiting
    let time: String
    let prompt: String
    let files: Int
    let commits: [String]

    var tooltip: String {
        var lines = ["\(time)  \(prompt)"]
        if files > 0 { lines.append("✎ edited \(files) file\(files == 1 ? "" : "s")") }
        lines += commits.map { "◆ \($0)" }
        let what: [String: String] = [
            "edit": "changed files", "run": "ran tools, changed nothing", "chat": "just conversation",
            "commit": "made a commit", "fail": "interrupted or errored", "active": "running now",
            "waiting": "waiting on you",
        ]
        if let w = what[kind] { lines.append(w) }
        return lines.joined(separator: "\n")
    }
}

struct SessionInfo: Decodable, Identifiable {
    let id: String
    let number: Int?
    let section: String
    let title: String
    let project: String
    let branch: String
    let agent: String
    let status: String
    let ringing: String
    let `where`: String
    let when: String
    let action: String
    let summary: String
    let recap: String
    let lastAsk: String
    let lastReply: String
    let turns: [TurnInfo]
    let hiddenTurns: Int
    let resumeCommand: String
    let archived: Bool
    let collisions: [CollisionInfo]
    let deletable: Bool  // closed, and its transcript is ours to move to the Trash

    var isRinging: Bool { !ringing.isEmpty }
    /// Clicking it takes you somewhere (a tab, a reopened window, the Codex app), so the panel should get out of the way.
    var hasDestination: Bool { isClosed || (!`where`.isEmpty && `where` != "bg") }
    var isClosed: Bool { status == "closed" }
    var place: String { branch.isEmpty ? project : "\(project) · \(branch)" }
    var about: String { summary.isEmpty ? recap : summary }
}

extension JSONDecoder {
    static let ttyl: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
}
