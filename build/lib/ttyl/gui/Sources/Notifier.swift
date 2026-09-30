import Foundation
import UserNotifications

/// Native notifications for rings. Clicking one goes to that session's terminal.
final class Notifier: NSObject, UNUserNotificationCenterDelegate {
    static let shared = Notifier()
    private weak var engine: Engine?

    func setUp(engine: Engine) {
        self.engine = engine
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound]) { granted, error in
            Log.write("notifications allowed: \(granted)" + (error.map { " (\($0.localizedDescription))" } ?? ""))
        }
    }

    func post(_ ring: RingEvent) {
        let content = UNMutableNotificationContent()
        content.title = "☎ ring ring"
        content.subtitle = "\(ring.project): \(ring.reason)"
        content.body = ring.what
        content.userInfo = ["id": ring.id]
        // no sound here: the engine plays the phone ring
        let request = UNNotificationRequest(identifier: "ring-\(ring.id)-\(Date().timeIntervalSince1970)",
                                            content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request) { error in
            if let error { Log.write("notification failed: \(error.localizedDescription)") }
        }
    }

    func post(_ alert: AlertEvent) {
        let content = UNMutableNotificationContent()
        content.title = alert.title
        content.body = alert.text
        content.userInfo = ["id": alert.id]
        let request = UNNotificationRequest(identifier: "alert-\(alert.id)-\(Date().timeIntervalSince1970)",
                                            content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request) { error in
            if let error { Log.write("notification failed: \(error.localizedDescription)") }
        }
    }

    // show banners even while the panel is open
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list])
    }

    // clicked: go to that session
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let id = response.notification.request.content.userInfo["id"] as? String
        Log.write("notification clicked: \(id ?? "?")")
        Task { @MainActor in
            if let id { self.engine?.go(id: id) }
            completionHandler()
        }
    }
}

enum Log {
    /// ~/.local/state/ttyl/app.log, for "why didn't it notify?"
    static func write(_ line: String) {
        let dir = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(".local/state/ttyl")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("app.log")
        let stamp = ISO8601DateFormatter().string(from: Date())
        let data = Data("\(stamp) \(line)\n".utf8)
        if let h = try? FileHandle(forWritingTo: file) {
            h.seekToEndOfFile()
            h.write(data)
            try? h.close()
        } else {
            try? data.write(to: file)
        }
    }
}
