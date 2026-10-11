// ttyl-summarize: writes a session summary with Apple's on-device model (macOS 26+, Apple
// Intelligence on), so summaries never leave the Mac. build.sh puts it next to the menu bar
// app; the engine (summarize.py) runs it and owns the prompt.
//
//   ttyl-summarize --check    {"available": true} or {"available": false, "reason": "..."}
//   ttyl-summarize < request  {"instructions": "...", "prompt": "..."} on stdin
//                             -> {"text": "..."}
//                             or {"error": "...", "kind": "unavailable|context|declined|other"}
import Foundation
import FoundationModels

func reply(_ object: [String: Any]) -> Never {
    let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(data + Data([0x0A]))
    exit(0)
}

func unavailableReason() -> String? {
    switch SystemLanguageModel.default.availability {
    case .available:
        return nil
    case .unavailable(.deviceNotEligible):
        return "this Mac can't run Apple Intelligence"
    case .unavailable(.appleIntelligenceNotEnabled):
        return "Apple Intelligence is off (System Settings > Apple Intelligence & Siri)"
    case .unavailable(.modelNotReady):
        return "Apple Intelligence is still downloading its model"
    case .unavailable(let other):
        return "Apple's on-device model is unavailable (\(other))"
    }
}

@main
struct Summarize {
    static func main() async {
        let checking = CommandLine.arguments.contains("--check")
        if let reason = unavailableReason() {
            reply(checking ? ["available": false, "reason": reason] : ["error": reason, "kind": "unavailable"])
        }
        if checking { reply(["available": true]) }

        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let request = (try? JSONSerialization.jsonObject(with: input)) as? [String: String],
              let prompt = request["prompt"] else {
            reply(["error": "expected {\"instructions\": ..., \"prompt\": ...} on stdin", "kind": "other"])
        }
        let session = LanguageModelSession(instructions: request["instructions"] ?? "")
        do {
            // low temperature: a summary should say what's in the log, not get creative
            let text = try await session.respond(to: prompt, options: GenerationOptions(temperature: 0.2)).content
            reply(["text": text])
        } catch let error as LanguageModelSession.GenerationError {
            switch error {
            case .exceededContextWindowSize:
                reply(["error": "too long for the on-device model", "kind": "context"])
            case .guardrailViolation, .refusal:
                reply(["error": "the on-device model declined", "kind": "declined"])
            default:
                reply(["error": "\(error)", "kind": "other"])
            }
        } catch {
            reply(["error": "\(error)", "kind": "other"])
        }
    }
}
