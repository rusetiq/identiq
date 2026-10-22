# Identiq iPhone capture

Native SwiftUI app for iOS 17+, using the **front TrueDepth camera**. It captures synchronized RGB photos and physical depth data with AVFoundation, guides five yaw poses, and exports a versioned JSON file via the iOS share sheet. It does not use the rear LiDAR scanner or Apple's private Face ID templates.

## Install on your phone

1. Open Xcode once and review/accept its license and any required platform setup. This machine currently has Xcode installed but its license is unaccepted, so the native build has **not been verified**.
2. Open `IdentiqCapture.xcodeproj` in Xcode.
3. In the target's **Signing & Capabilities**, select your Apple development team. Change the bundle identifier if needed.
4. Connect a physical iPhone with a front TrueDepth camera. Enable Developer Mode if requested. Select that phone as the run destination and click Run.

The simulator has no TrueDepth sensor; it cannot validate depth capture. No internet connection, local network permission, server pairing, or cloud account is required by the app.

After setup, an unsigned simulator build can check compilation:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild \
  -project ios/IdentiqCapture.xcodeproj -scheme IdentiqCapture \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /private/tmp/identiq-ios-build CODE_SIGNING_ALLOWED=NO build
```

## Enroll

Enter a name and confirm permission. Tap **Start TrueDepth capture**. Keep your face centered 20–85 cm from the camera, and follow the five guided poses. Share the finished file through AirDrop to your laptop or save it to Files.

On the laptop, open Identiq, choose **Enroll → iPhone scan**, select the JSON file, confirm permission, and import. **Save to** can create a new person using the capture's name, or add five samples to an existing person after checking they match. Existing names remain unchanged. Each person can have one phone capture; remove and reenroll to replace it.

The export contains photos and is sensitive. The companion clears in-memory samples after export and deletes its temporary copy when the share sheet is dismissed or capture is discarded. Copies saved or transferred by you remain until you delete them. The laptop stores embeddings and sanitized depth/source metadata, not the imported photos.

## What depth currently does

The app rejects missing/dropped depth, multiple faces, off-center faces, unsupported RGB/depth aspect alignment, incomplete pose coverage, implausible face distances, insufficient valid depth, and very flat/extreme depth spread. These are **experimental quality heuristics**. They do not establish identity or robust liveness. Files are not signed or hardware-attested, and their depth summaries can be forged.

Recognition uses the existing ArcFace RGB matcher. Depth is not part of its embedding, so better accuracy has not been demonstrated. True 3D identity matching would require a separate model, calibration and validation, plus depth capture during verification; the laptop's ordinary RGB camera cannot supply that depth.

Physical-device validation must check whether both output connections rotate the RGB and depth buffers alike, face-region alignment, Vision yaw direction, depth format/frame-rate support, and capture thresholds. No fallback to camera-only or estimated ARKit geometry is labeled as TrueDepth.

## Capture format v1

The bundle has `format: "identiq.capture"`, `version: 1`, `sensor: "truedepth"`, `name`, `consent: true`, `device`, `captured_at` (ISO 8601 with timezone), and exactly five `samples`:

- `pose`: `front`, `left`, `right`, `left_more`, `right_more`.
- `yaw_radians`: target angles 0, −0.15, +0.15, −0.30, +0.30; capture tolerance 0.10, import tolerance 0.14.
- `image`: `data:image/jpeg;base64,...`, longest edge at most 960 pixels from the companion.
- `depth`: `valid_fraction` (0.65–1), `median_m` (0.2–0.85), `spread_m` (0.004–0.18, 90th minus 10th percentile of finite depth in the central face region).

The laptop also performs image/face quality checks, identity consistency checks, duplicate detection, and a match to the chosen existing identity before committing changes. All import writes are atomic.

Sources: [Apple: streaming TrueDepth](https://developer.apple.com/documentation/avfoundation/streaming-depth-data-from-the-truedepth-camera), [Apple: synchronized outputs](https://developer.apple.com/documentation/avfoundation/avcapturedataoutputsynchronizer), [Apple: LiDAR capture](https://developer.apple.com/documentation/avfoundation/capturing-depth-using-the-lidar-camera).
