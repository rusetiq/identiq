import AVFoundation
import Combine
import CoreImage
import Foundation
import UIKit
import Vision

struct DepthSummary: Codable {
    let valid_fraction: Double
    let median_m: Double
    let spread_m: Double
}
struct FaceSample: Codable {
    let pose: String
    let yaw_radians: Double
    let image: String
    let depth: DepthSummary
}
struct CaptureFile: Codable {
    let format = "identiq.capture"
    let version = 1
    let sensor = "truedepth"
    let name: String
    let consent = true
    let device: String
    let captured_at: String
    let samples: [FaceSample]
}

/// All camera configuration and capture state stays on the serial work queue.
final class CaptureController: NSObject, ObservableObject, AVCaptureDataOutputSynchronizerDelegate {
    @Published private(set) var message = "Keep one face centered, 20–85 cm from the phone."
    @Published private(set) var count = 0
    @Published private(set) var running = false
    @Published private(set) var exportURL: URL?
    let session = AVCaptureSession()
    private let queue = DispatchQueue(label: "identiq.capture")
    private let video = AVCaptureVideoDataOutput()
    private let depth = AVCaptureDepthDataOutput()
    private var synchronizer: AVCaptureDataOutputSynchronizer?
    private let context = CIContext()
    private var samples: [FaceSample] = []
    private var personName = ""
    private var capturing = false
    private var configured = false
    private var attemptID = UUID() // Main-thread permission request cancellation.
    private var activeID = UUID() // Work-queue capture generation.
    private var lastAccepted = Date.distantPast
    private var lastMessage = ""
    private let poses: [(String, Double, String)] = [
        ("front", 0, "Look straight ahead."),
        ("left", -0.15, "Turn slowly to your left. Keep your face in the oval."),
        ("right", 0.15, "Turn slowly to your right."),
        ("left_more", -0.30, "Turn a little further to your left."),
        ("right_more", 0.30, "Turn a little further to your right.")
    ]

    func start(name: String) {
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (1...80).contains(clean.count), !clean.unicodeScalars.contains(where: { $0.value < 32 }) else {
            message = "Enter a name with 1–80 characters."
            return
        }
        running = true
        message = "Opening TrueDepth camera…"
        let requestID = UUID()
        attemptID = requestID
        AVCaptureDevice.requestAccess(for: .video) { allowed in
          DispatchQueue.main.async {
            guard self.attemptID == requestID else { return }
            guard allowed else { self.running = false; self.message = "Allow camera access in Settings, then try again."; return }
            self.queue.async {
                do {
                    try self.configure()
                    self.activeID = requestID
                    self.samples = []
                    self.personName = clean
                    self.lastAccepted = .distantPast
                    self.capturing = true
                    DispatchQueue.main.async { self.count = 0 }
                    self.session.startRunning()
                    self.updateMessage(self.poses[0].2)
                    self.queue.asyncAfter(deadline: .now() + 90) {
                        guard self.capturing, self.activeID == requestID else { return }
                        self.capturing = false
                        self.samples.removeAll()
                        self.session.stopRunning()
                        DispatchQueue.main.async {
                            self.running = false; self.count = 0
                            self.message = "Capture timed out. Check lighting and depth-camera availability, then start again."
                        }
                    }
                } catch {
                    self.capturing = false
                    DispatchQueue.main.async { self.running = false; self.message = error.localizedDescription }
                }
            }
          }
        }
    }

    private func configure() throws {
        guard !configured else { return }
        guard let camera = AVCaptureDevice.default(.builtInTrueDepthCamera, for: .video, position: .front) else {
            throw failure("This device has no front TrueDepth camera. Use laptop enrollment instead.")
        }
        session.beginConfiguration()
        defer { session.commitConfiguration() }
        session.sessionPreset = .inputPriority
        for input in session.inputs { session.removeInput(input) }
        for output in session.outputs { session.removeOutput(output) }
        let input = try AVCaptureDeviceInput(device: camera)
        guard session.canAddInput(input) else { throw failure("Cannot open the front camera.") }
        session.addInput(input)
        let formats = camera.formats.filter {
            let dimensions = CMVideoFormatDescriptionGetDimensions($0.formatDescription)
            return $0.supportedDepthDataFormats.contains { depthFormat in
                depthFormat.videoSupportedFrameRateRanges.contains { $0.minFrameRate <= 15 && $0.maxFrameRate >= 15 }
            } && dimensions.width <= 1280 &&
                $0.videoSupportedFrameRateRanges.contains { $0.minFrameRate <= 15 && $0.maxFrameRate >= 15 }
        }
        guard let format = formats.max(by: {
            CMVideoFormatDescriptionGetDimensions($0.formatDescription).width < CMVideoFormatDescriptionGetDimensions($1.formatDescription).width
        }), let depthFormat = format.supportedDepthDataFormats.filter({
            $0.videoSupportedFrameRateRanges.contains { $0.minFrameRate <= 15 && $0.maxFrameRate >= 15 }
        }).max(by: {
            CMVideoFormatDescriptionGetDimensions($0.formatDescription).width < CMVideoFormatDescriptionGetDimensions($1.formatDescription).width
        }) else { throw failure("No supported synchronized TrueDepth format is available.") }
        try camera.lockForConfiguration()
        camera.activeFormat = format
        camera.activeDepthDataFormat = depthFormat
        camera.activeVideoMinFrameDuration = CMTime(value: 1, timescale: 15)
        camera.activeVideoMaxFrameDuration = CMTime(value: 1, timescale: 15)
        camera.unlockForConfiguration()
        video.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        video.alwaysDiscardsLateVideoFrames = true
        depth.isFilteringEnabled = true
        guard session.canAddOutput(video), session.canAddOutput(depth) else { throw failure("Synchronized camera outputs are unavailable.") }
        session.addOutput(video)
        session.addOutput(depth)
        // Rotate both delivered pixel buffers alike; preview can mirror independently.
        for connection in [video.connection(with: .video), depth.connection(with: .depthData)].compactMap({ $0 }) {
            if connection.isVideoOrientationSupported { connection.videoOrientation = .portrait }
            if connection.isVideoMirroringSupported {
                connection.automaticallyAdjustsVideoMirroring = false
                connection.isVideoMirrored = false
            }
        }
        synchronizer = AVCaptureDataOutputSynchronizer(dataOutputs: [video, depth])
        synchronizer?.setDelegate(self, queue: queue)
        configured = true
    }

    func dataOutputSynchronizer(_ synchronizer: AVCaptureDataOutputSynchronizer, didOutput collection: AVCaptureSynchronizedDataCollection) {
        guard capturing, samples.count < 5, Date().timeIntervalSince(lastAccepted) > 0.8,
              let rgb = collection.synchronizedData(for: video) as? AVCaptureSynchronizedSampleBufferData,
              let measurement = collection.synchronizedData(for: depth) as? AVCaptureSynchronizedDepthData,
              !rgb.sampleBufferWasDropped, !measurement.depthDataWasDropped,
              let pixels = CMSampleBufferGetImageBuffer(rgb.sampleBuffer) else { return }
        do {
            let request = VNDetectFaceRectanglesRequest()
            try VNImageRequestHandler(cvPixelBuffer: pixels, orientation: .up).perform([request])
            guard let faces = request.results, faces.count == 1 else { updateMessage("Keep exactly one face in the frame."); return }
            let face = faces[0]
            let box = face.boundingBox
            guard abs(box.midX - 0.5) < 0.16, abs(box.midY - 0.5) < 0.18, box.width > 0.23 else {
                updateMessage("Center your face in the oval and move a little closer."); return
            }
            guard let yaw = face.yaw?.doubleValue, abs(face.roll?.doubleValue ?? 0) < 0.20 else {
                updateMessage("Keep your head upright."); return
            }
            let target = poses[samples.count]
            guard abs(yaw - target.1) <= 0.10 else { updateMessage(target.2); return }
            let map = measurement.depthData.converting(toDepthDataType: kCVPixelFormatType_DepthFloat32).depthDataMap
            let rgbRatio = Double(CVPixelBufferGetWidth(pixels)) / Double(CVPixelBufferGetHeight(pixels))
            let depthRatio = Double(CVPixelBufferGetWidth(map)) / Double(CVPixelBufferGetHeight(map))
            guard abs(rgbRatio - depthRatio) < 0.1 else { updateMessage("RGB and depth alignment is unsupported on this configuration."); return }
            guard let summary = depthSummary(map, face: box) else {
                updateMessage("Hold still, face the phone, and stay 20–85 cm away."); return
            }
            let image = CIImage(cvPixelBuffer: pixels)
            let scale = min(1, 960 / max(image.extent.width, image.extent.height))
            let resized = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
            guard let cgImage = context.createCGImage(resized, from: resized.extent),
                  let jpeg = UIImage(cgImage: cgImage).jpegData(compressionQuality: 0.9) else { return }
            samples.append(FaceSample(pose: target.0, yaw_radians: yaw,
                image: "data:image/jpeg;base64," + jpeg.base64EncodedString(), depth: summary))
            lastAccepted = Date()
            let total = samples.count
            DispatchQueue.main.async { self.count = total }
            if total == 5 { try complete() } else { updateMessage(poses[total].2) }
        } catch { updateMessage("Capture could not be processed. Try again: \(error.localizedDescription)") }
    }

    private func depthSummary(_ pixels: CVPixelBuffer, face: CGRect) -> DepthSummary? {
        CVPixelBufferLockBaseAddress(pixels, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixels, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(pixels) else { return nil }
        let width = CVPixelBufferGetWidth(pixels), height = CVPixelBufferGetHeight(pixels)
        let stride = CVPixelBufferGetBytesPerRow(pixels) / MemoryLayout<Float>.size
        let values = base.assumingMemoryBound(to: Float.self)
        // Vision boxes use a bottom-left origin. Buffer rows start at top-left.
        let region = face.insetBy(dx: face.width * 0.2, dy: face.height * 0.2)
        let x0 = max(0, Int(region.minX * Double(width))), x1 = min(width, Int(region.maxX * Double(width)))
        let y0 = max(0, Int((1 - region.maxY) * Double(height))), y1 = min(height, Int((1 - region.minY) * Double(height)))
        guard x1 > x0, y1 > y0 else { return nil }
        var valid: [Double] = []
        for y in y0..<y1 {
            for x in x0..<x1 {
                let value = Double(values[y * stride + x])
                if value.isFinite && value > 0.1 && value < 2 { valid.append(value) }
            }
        }
        let fraction = Double(valid.count) / Double((x1 - x0) * (y1 - y0))
        guard fraction >= 0.65, valid.count > 20 else { return nil }
        valid.sort()
        let median = valid[valid.count / 2]
        let spread = valid[Int(Double(valid.count - 1) * 0.9)] - valid[Int(Double(valid.count - 1) * 0.1)]
        guard (0.2...0.85).contains(median), (0.004...0.18).contains(spread) else { return nil }
        return DepthSummary(valid_fraction: fraction, median_m: median, spread_m: spread)
    }

    private func complete() throws {
        capturing = false
        session.stopRunning()
        let bundle = CaptureFile(name: personName, device: "\(UIDevice.current.model) / iOS \(UIDevice.current.systemVersion)",
            captured_at: ISO8601DateFormatter().string(from: Date()), samples: samples)
        do {
            let data = try JSONEncoder().encode(bundle)
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("identiq-\(UUID().uuidString).json")
            try data.write(to: url, options: [.atomic, .completeFileProtection])
            samples.removeAll()
            let finishedID = activeID
            DispatchQueue.main.async {
                guard self.attemptID == finishedID else { try? FileManager.default.removeItem(at: url); return }
                self.exportURL = url; self.running = false; self.message = "Five samples captured. Share the file to your laptop."
            }
        } catch {
            samples.removeAll()
            DispatchQueue.main.async { self.running = false; self.message = "Could not export capture. Start again." }
            throw error
        }
    }

    func cancel() {
        attemptID = UUID()
        discardExport()
        queue.async {
            self.capturing = false
            self.samples.removeAll()
            self.session.stopRunning()
            DispatchQueue.main.async { self.running = false; self.count = 0; self.message = "Capture cleared. Ready to start again." }
        }
    }
    func discardExport() {
        if let url = exportURL { try? FileManager.default.removeItem(at: url) }
        exportURL = nil
    }
    private func updateMessage(_ text: String) {
        guard lastMessage != text else { return }
        lastMessage = text
        DispatchQueue.main.async { self.message = text }
    }
    private func failure(_ text: String) -> NSError { NSError(domain: "Identiq", code: 1, userInfo: [NSLocalizedDescriptionKey: text]) }
}
