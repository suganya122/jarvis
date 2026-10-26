// `Jarvis --speech`: Apple's speech recognition, on this Mac only, for the assistant's voice
// worker (workers/voice/apple.py). One JSON object per line:
//   in:  {"type":"open","id":"s1","rate":16000}  {"type":"audio","id":"s1","pcm_b64":"…"}
//        {"type":"close","id":"s1","abort":false}
//   out: {"type":"ready","ok":true,"detail":"…"}  {"type":"partial"|"final","id":"s1","text":"…"}
import AVFoundation
import Foundation
import Speech

final class SpeechServer {
    /// One utterance as the voice worker sees it. Apple ends a recognition task on its own (a few
    /// seconds without speech, a long pause, its time limit); the stream then carries on with a new
    /// task on the next audio, keeping what was already heard, until the worker closes it.
    private final class Stream {
        var request = SFSpeechAudioBufferRecognitionRequest()
        var task: SFSpeechRecognitionTask?
        var heard = ""  // words from tasks that already ended
        var last = ""  // everything heard so far, as last sent
        var closing = false
        var done = false
        var restarted = false
    }

    private let recognizer: SFSpeechRecognizer?
    private var streams: [String: Stream] = [:]
    private let queue = DispatchQueue(label: "jarvis.speech")
    private let output = NSLock()
    private var authorized = false

    init() {
        let wanted = Locale.current.identifier.hasPrefix("en") ? Locale.current : Locale(identifier: "en-US")
        recognizer = SFSpeechRecognizer(locale: wanted) ?? SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    }

    func run() -> Never {
        SFSpeechRecognizer.requestAuthorization { status in
            self.queue.async {
                self.authorized = status == .authorized
                guard let recognizer = self.recognizer else {
                    self.emit(["type": "ready", "ok": false, "detail": "no speech recognizer for this language"])
                    return
                }
                let local = recognizer.supportsOnDeviceRecognition
                let detail = self.authorized
                    ? (local ? "Apple speech recognition, on this Mac" : "Apple speech recognition (this Mac can't do it offline)")
                    : "Speech Recognition is not allowed for Jarvis"
                self.emit(["type": "ready", "ok": self.authorized, "detail": detail])
            }
        }
        DispatchQueue.global().async {
            while let line = readLine(strippingNewline: true) {
                guard let data = line.data(using: .utf8),
                      let message = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                self.queue.async { self.handle(message) }
            }
            exit(0)  // the voice worker closed our input: done
        }
        dispatchMain()
    }

    private func emit(_ message: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: message) else { return }
        output.lock()
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([0x0A]))
        output.unlock()
    }

    private func handle(_ message: [String: Any]) {
        guard let type = message["type"] as? String, let id = message["id"] as? String else { return }
        switch type {
        case "open": open(id)
        case "audio":
            guard let stream = streams[id], let b64 = message["pcm_b64"] as? String,
                  let pcm = Data(base64Encoded: b64), let buffer = Self.buffer(pcm) else { return }
            if stream.task == nil { start(stream, id) }  // the last task ended: listen on with a new one
            stream.request.append(buffer)
        case "close":
            guard let stream = streams[id] else { return }
            if (message["abort"] as? Bool) == true {
                stream.done = true
                stream.task?.cancel()
                streams[id] = nil
            } else if stream.task == nil {
                end(stream, id)  // nothing in flight: what was heard is final
            } else {
                stream.closing = true
                stream.request.endAudio()  // the final result arrives through the task's handler
            }
        default: break
        }
    }

    private func open(_ id: String) {
        guard authorized, recognizer != nil else {
            emit(["type": "final", "id": id, "text": ""])
            return
        }
        let stream = Stream()
        streams[id] = stream
        start(stream, id)
    }

    private func start(_ stream: Stream, _ id: String) {
        guard let recognizer = recognizer else { return }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        if recognizer.supportsOnDeviceRecognition {
            request.requiresOnDeviceRecognition = true  // never sent to Apple's servers
        }
        if #available(macOS 13, *) {
            request.addsPunctuation = false
        }
        // Words it should expect: the wake word and the phrases that steer a conversation.
        request.contextualStrings = ["Jarvis", "Hey Jarvis", "OK Jarvis", "go offline", "that's all",
                                     "stop listening", "yes", "no"]
        stream.request = request
        stream.task = recognizer.recognitionTask(with: request) { result, error in
            self.queue.async {
                guard !stream.done, stream.request === request else { return }  // an older task
                if let result = result {
                    let text = Self.joined(stream.heard, result.bestTranscription.formattedString)
                    if result.isFinal {
                        self.taskEnded(stream, id, text)
                    } else if text != stream.last {
                        stream.last = text
                        self.emit(["type": "partial", "id": id, "text": text])
                    }
                } else if let error = error {
                    if !stream.closing && !stream.restarted {  // once per utterance: the log stays short
                        stream.restarted = true
                        Self.log("speech: Apple ended a recognition task (\(error.localizedDescription)); listening on")
                    }
                    self.taskEnded(stream, id, stream.last)
                }
            }
        }
    }

    private func taskEnded(_ stream: Stream, _ id: String, _ text: String) {
        stream.task = nil
        stream.heard = text
        if text != stream.last {
            stream.last = text
            emit(["type": "partial", "id": id, "text": text])
        }
        if stream.closing { end(stream, id) }
    }

    private func end(_ stream: Stream, _ id: String) {
        stream.done = true
        streams[id] = nil
        emit(["type": "final", "id": id, "text": stream.last])
    }

    private static func joined(_ before: String, _ more: String) -> String {
        before.isEmpty ? more : more.isEmpty ? before : before + " " + more
    }

    private static func log(_ line: String) {
        FileHandle.standardError.write((line + "\n").data(using: .utf8)!)
    }

    private static let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000,
                                              channels: 1, interleaved: false)!

    /// 16-bit little-endian PCM at 16 kHz -> the float buffer the recognizer takes.
    private static func buffer(_ pcm: Data) -> AVAudioPCMBuffer? {
        let count = pcm.count / 2
        guard count > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)),
              let channel = buffer.floatChannelData?[0] else { return nil }
        pcm.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            let samples = raw.bindMemory(to: Int16.self)
            for i in 0..<count { channel[i] = Float(Int16(littleEndian: samples[i])) / 32768.0 }
        }
        buffer.frameLength = AVAudioFrameCount(count)
        return buffer
    }
}
