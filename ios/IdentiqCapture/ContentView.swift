import AVFoundation
import SwiftUI
import UIKit

struct ContentView: View {
    @StateObject private var capture = CaptureController()
    @State private var name = ""
    @State private var consent = false
    @State private var sharing = false
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("Capture on iPhone. Recognize on your laptop.").foregroundStyle(.secondary)
                    CameraPreview(session: capture.session)
                        .frame(height: 320).clipShape(RoundedRectangle(cornerRadius: 14))
                        .overlay { RoundedRectangle(cornerRadius: 100).stroke(.white.opacity(0.5), lineWidth: 2).frame(width: 195, height: 265) }
                    TextField("Person’s name", text: $name)
                        .textContentType(.name).textFieldStyle(.roundedBorder).disabled(capture.running || capture.exportURL != nil)
                    Toggle("I have permission to capture and enroll this person.", isOn: $consent)
                        .font(.footnote).disabled(capture.running || capture.exportURL != nil)
                    HStack {
                        ForEach(0..<5) { index in
                            Image(systemName: index < capture.count ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(index < capture.count ? Color.green : Color.gray)
                        }
                        Spacer()
                        Text("\(capture.count) / 5").foregroundStyle(.secondary)
                    }
                    Text(capture.message).font(.subheadline).accessibilityAddTraits(.updatesFrequently)
                    if capture.running {
                        Button("Cancel capture") { capture.cancel() }.buttonStyle(.bordered)
                    } else if capture.exportURL != nil {
                        Button("Share capture file") { sharing = true }.buttonStyle(.borderedProminent)
                        Button("Discard and start again") { capture.cancel() }.buttonStyle(.bordered)
                    } else {
                        Button("Start TrueDepth capture") { capture.start(name: name) }
                            .buttonStyle(.borderedProminent)
                            .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !consent)
                    }
                    Text("Share via AirDrop or save to Files, then use iPhone scan → Import enrollment on your laptop. The exported file contains face photos; delete transferred copies when finished.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("Depth checks are experimental. This is not Face ID authentication or verified liveness detection.")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding()
            }.navigationTitle("Identiq capture")
                .sheet(isPresented: $sharing, onDismiss: { capture.discardExport() }) {
                    if let url = capture.exportURL { ExportSheet(url: url) }
                }
                .onChange(of: scenePhase) { phase in
                    if phase == .background && capture.running { capture.cancel() }
                }
        }
    }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession
    class Preview: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }
    func makeUIView(context: Context) -> Preview {
        let view = Preview()
        view.backgroundColor = .darkGray
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspectFill
        view.previewLayer.connection?.videoOrientation = .portrait
        return view
    }
    func updateUIView(_ uiView: Preview, context: Context) {}
}

struct ExportSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
