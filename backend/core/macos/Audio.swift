// `Jarvis --audio`: the microphone and the voice in one AVAudioEngine, with Apple's voice
// processing (echo cancellation) on, so what the assistant says is taken out of what the
// microphone hears and the person can talk over it. For the ears worker (workers/ears/mac_audio.py).
// One JSON object per line:
//   in:  {"type":"listen","on":true}  {"type":"say","id":"s1","text":"…"}
//        {"type":"play","id":"s2","pcm_b64":"…","rate":24000,"last":false}  {"type":"stop"}
//   out: {"type":"ready","echo_cancel":true,"microphone":"…","detail":"…"}
//        {"type":"frame","pcm_b64":"…"}   (16 kHz mono 16-bit, 100 ms)
//        {"type":"said","id":"s1","ok":true,"detail":""}   {"type":"problem","detail":"…"}
import AVFoundation
import Foundation

final class AudioServer {
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let synthesizer = AVSpeechSynthesizer()
    private let playFormat = AVAudioFormat(standardFormatWithSampleRate: 48_000, channels: 1)!
    private var converters: [String: AVAudioConverter] = [:]
    private var echoCancel = false
    private var listening = false
    private var lastInput = Date()
    private var block: [Int16] = []
    private var resampleAt = 0.0  // position in the input stream, in input frames

    // what's being said: one id at a time; a new say or stop ends the old one
    private var current: String?
    private var pending = 0
    private var allQueued = false

    func run() -> Never {
        DispatchQueue.main.async { self.setUp() }
        Thread.detachNewThread {
            while let line = readLine(strippingNewline: true) {
                DispatchQueue.main.async { self.serve(line) }
            }
            DispatchQueue.main.async { exit(0) }  // the ears worker closed our input
        }
        Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { _ in self.watchdog() }
        RunLoop.main.run()
        exit(0)
    }

    // ---- set up --------------------------------------------------------------------------

    private func setUp() {
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: playFormat)  // before voice processing
        do {
            try engine.inputNode.setVoiceProcessingEnabled(true)
            echoCancel = true
            if #available(macOS 14.0, *) {  // don't turn down the user's music while it listens
                engine.inputNode.voiceProcessingOtherAudioDuckingConfiguration =
                    AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: false, duckingLevel: .min)
            }
        } catch {
            note("echo cancellation is off: \(error.localizedDescription)")
        }
        installTap()
        do {
            try engine.start()
        } catch {
            fallBack("couldn't start audio: \(error.localizedDescription)")
        }
        emit(["type": "ready", "echo_cancel": echoCancel, "microphone": Self.inputName(),
              "detail": echoCancel ? "echo cancellation on" : "echo cancellation off"])
    }

    private func installTap() {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 4_800, format: format) { buffer, _ in
            DispatchQueue.main.async { self.capture(buffer) }
        }
    }

    /// Voice processing failed or starved the microphone: carry on without it (the worker's
    /// transcript check still tells its own voice apart, just less surely).
    private func fallBack(_ why: String) {
        guard echoCancel else { return note(why) }
        note("\(why); continuing without echo cancellation")
        engine.stop()
        try? engine.inputNode.setVoiceProcessingEnabled(false)
        echoCancel = false
        installTap()
        try? engine.start()
    }

    private func watchdog() {
        if listening && Date().timeIntervalSince(lastInput) > 3 {
            lastInput = Date()
            fallBack("the microphone went quiet")
        }
    }

    // ---- the microphone -------------------------------------------------------------------

    private func capture(_ buffer: AVAudioPCMBuffer) {
        lastInput = Date()
        guard listening, let channel = buffer.floatChannelData?[0] else { return }
        // channel 0 only: with voice processing the input can arrive as copies on several channels
        let rate = buffer.format.sampleRate
        let step = rate / 16_000
        let count = Double(buffer.frameLength)
        while resampleAt < count {
            let i = Int(resampleAt)
            let next = min(i + 1, Int(buffer.frameLength) - 1)
            let frac = Float(resampleAt - Double(i))
            let sample = channel[i] * (1 - frac) + channel[next] * frac
            block.append(Int16(max(-1, min(1, sample)) * 32_767))
            resampleAt += step
        }
        resampleAt -= count
        while block.count >= 1_600 {
            let chunk = block.prefix(1_600)
            block.removeFirst(1_600)
            let data = chunk.withContiguousStorageIfAvailable { Data(buffer: $0) } ?? Data()
            emit(["type": "frame", "pcm_b64": data.base64EncodedString()])
        }
    }

    // ---- speaking --------------------------------------------------------------------------

    private func say(_ id: String, _ text: String) {
        stop(reason: "interrupted")
        (current, pending, allQueued) = (id, 0, false)
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = Self.bestVoice()
        synthesizer.write(utterance) { buffer in
            guard let pcm = buffer as? AVAudioPCMBuffer else { return }
            let copy = Self.copy(pcm)
            DispatchQueue.main.async { self.queue(copy, for: id) }
        }
    }

    private func play(_ id: String, _ pcm: Data, rate: Double, last: Bool) {
        if current != id {
            stop(reason: "interrupted")
            (current, pending, allQueued) = (id, 0, false)
        }
        let samples = pcm.count / 2
        if samples > 0, let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1,
                                                   interleaved: false),
           let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples)),
           let out = buffer.floatChannelData?[0] {
            pcm.withUnsafeBytes { raw in
                let ints = raw.bindMemory(to: Int16.self)
                for i in 0..<samples { out[i] = Float(Int16(littleEndian: ints[i])) / 32_768 }
            }
            buffer.frameLength = AVAudioFrameCount(samples)
            queue(buffer, for: id)
        }
        if last { queue(nil, for: id) }
    }

    /// nil (or an empty buffer) marks the end of what's to be said.
    private func queue(_ buffer: AVAudioPCMBuffer?, for id: String) {
        guard current == id else { return }
        guard let buffer = buffer, buffer.frameLength > 0 else {
            allQueued = true
            return finishIfDone(id)
        }
        guard let converted = convert(buffer) else { return }
        pending += 1
        player.scheduleBuffer(converted) {
            DispatchQueue.main.async {
                guard self.current == id else { return }
                self.pending -= 1
                self.finishIfDone(id)
            }
        }
        if !engine.isRunning { try? engine.start() }
        if !player.isPlaying { player.play() }
    }

    private func finishIfDone(_ id: String) {
        guard current == id, allQueued, pending == 0 else { return }
        current = nil
        emit(["type": "said", "id": id, "ok": true, "detail": ""])
    }

    private func stop(reason: String) {
        synthesizer.stopSpeaking(at: .immediate)
        player.stop()
        if let id = current {
            current = nil
            emit(["type": "said", "id": id, "ok": false, "detail": reason])
        }
    }

    private func convert(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        let key = "\(buffer.format)"
        if converters[key] == nil { converters[key] = AVAudioConverter(from: buffer.format, to: playFormat) }
        guard let converter = converters[key] else { return nil }
        let ratio = playFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: capacity) else { return nil }
        var given = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if given {
                status.pointee = .noDataNow
                return nil
            }
            given = true
            status.pointee = .haveData
            return buffer
        }
        return error == nil ? out : nil
    }

    // ---- protocol ----------------------------------------------------------------------------

    private func serve(_ line: String) {
        guard let data = line.data(using: .utf8),
              let m = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = m["type"] as? String else { return }
        switch type {
        case "listen":
            listening = (m["on"] as? Bool) ?? false
            lastInput = Date()
            block.removeAll()
        case "say": say((m["id"] as? String) ?? "", (m["text"] as? String) ?? "")
        case "play":
            play((m["id"] as? String) ?? "", Data(base64Encoded: (m["pcm_b64"] as? String) ?? "") ?? Data(),
                 rate: (m["rate"] as? Double) ?? 24_000, last: (m["last"] as? Bool) ?? false)
        case "stop": stop(reason: "stopped")
        default: break
        }
    }

    private func note(_ detail: String) {
        emit(["type": "problem", "detail": detail])
    }

    private func emit(_ message: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: message) else { return }
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([0x0A]))
    }

    // ---- helpers -------------------------------------------------------------------------------

    /// The synthesizer reuses its buffers: keep a copy of each before handing it on.
    private static func copy(_ pcm: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let copy = AVAudioPCMBuffer(pcmFormat: pcm.format, frameCapacity: max(pcm.frameLength, 1)) else { return nil }
        copy.frameLength = pcm.frameLength
        let bytes = Int(pcm.frameLength) * Int(pcm.format.streamDescription.pointee.mBytesPerFrame)
        let channels = Int(pcm.format.channelCount)
        if let src = pcm.floatChannelData, let dst = copy.floatChannelData {
            for c in 0..<channels { memcpy(dst[c], src[c], bytes) }
        } else if let src = pcm.int16ChannelData, let dst = copy.int16ChannelData {
            for c in 0..<channels { memcpy(dst[c], src[c], bytes) }
        } else if let src = pcm.int32ChannelData, let dst = copy.int32ChannelData {
            for c in 0..<channels { memcpy(dst[c], src[c], bytes) }
        }
        return copy
    }

    /// English, the best quality installed (Premium, then Enhanced), this Mac's region first.
    static func bestVoice() -> AVSpeechSynthesisVoice? {
        let region = Locale.current.identifier.replacingOccurrences(of: "_", with: "-")
        let english = AVSpeechSynthesisVoice.speechVoices().filter { $0.language.hasPrefix("en") }
        func rank(_ v: AVSpeechSynthesisVoice) -> Int {
            var score = 0
            if #available(macOS 13.0, *), v.quality == .premium { score += 20 }
            if v.quality == .enhanced { score += 10 }
            if v.language == region { score += 2 }
            return score
        }
        return english.max { rank($0) < rank($1) } ?? AVSpeechSynthesisVoice(language: "en-US")
    }

    static func inputName() -> String {
        AVCaptureDevice.default(for: .audio)?.localizedName ?? "default microphone"
    }
}
