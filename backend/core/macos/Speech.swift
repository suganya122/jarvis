// `Jarvis --speech`: Apple's speech recognition, on this Mac only, for the assistant's voice
// worker (workers/voice/apple.py). One JSON object per line:
//   in:  {"type":"open","id":"s1","rate":16000}  {"type":"audio","id":"s1","pcm_b64":"…"}
//        {"type":"close","id":"s1","abort":false}
//   out: {"type":"ready","ok":true,"detail":"…"}  {"type":"partial"|"final","id":"s1","text":"…"}
import AVFoundation
import Foundation
import Speech

final class SpeechServer {
    private final class Stream {
        let request = SFSpeechAudioBufferRecognitionRequest()
        var task: SFSpeechRecognitionTask?
        var last = ""
        var done = false
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
            stream.request.append(buffer)
        case "close":
            guard let stream = streams[id] else { return }
            if (message["abort"] as? Bool) == true {
                stream.done = true
                stream.task?.cancel()
                streams[id] = nil
            } else {
                stream.request.endAudio()  // the final result arrives through the task's handler
            }
        default: break
        }
    }

    private func open(_ id: String) {
        guard authorized, let recognizer = recognizer else {
            emit(["type": "final", "id": id, "text": ""])
            return
        }
        let stream = Stream()
        stream.request.shouldReportPartialResults = true
        if recognizer.supportsOnDeviceRecognition {
            stream.request.requiresOnDeviceRecognition = true  // never sent to Apple's servers
        }
        if #available(macOS 13, *) {
            stream.request.addsPunctuation = false
        }
        // Words it should expect: the wake word and the phrases that steer a conversation.
        stream.request.contextualStrings = ["Jarvis", "Hey Jarvis", "OK Jarvis", "go offline", "that's all",
                                            "stop listening", "yes", "no"]
        streams[id] = stream
        stream.task = recognizer.recognitionTask(with: stream.request) { result, error in
            self.queue.async {
                guard !stream.done else { return }
                if let result = result {
                    let text = result.bestTranscription.formattedString
                    if result.isFinal {
                        stream.done = true
                        self.streams[id] = nil
                        self.emit(["type": "final", "id": id, "text": text])
                    } else if text != stream.last {
                        stream.last = text
                        self.emit(["type": "partial", "id": id, "text": text])
                    }
                } else if error != nil {
                    stream.done = true
                    self.streams[id] = nil
                    self.emit(["type": "final", "id": id, "text": stream.last])
                }
            }
        }
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
