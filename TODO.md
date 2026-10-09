# Simpler UI and iPhone enrollment

## Implementation

- [x] Replace the dashboard with a camera, Recognize / Enroll controls, and a compact people list.
- [x] Add an iPhone enrollment import option, with permission confirmation and an existing-person target.
- [x] Define a versioned capture format with five JPEGs, capture poses, TrueDepth depth summaries, and device metadata.
- [x] Validate imports, reject inconsistent/duplicate identities, and store source metadata with embeddings.
- [x] Write the native SwiftUI companion for synchronized front TrueDepth RGB and depth capture, guided poses, depth checks, and local share-sheet export. Hardware behavior remains unverified.
- [x] Provide an Xcode project and setup instructions. Keep the laptop server on localhost; transfer via AirDrop or Files.
- [x] Test laptop workflows, iPhone imports, database migration, and attachment to existing identities: 54 tests passed; browser integration passed with a simulated camera and synthetic depth metadata.
- [x] Validate Swift syntax and project/property-list structure.
- [ ] Build the iOS companion. Blocked by Xcode's unaccepted license; the owner must review and accept it in Xcode before a build can run.

## Device validation still required

- [ ] Install and run the companion on a physical TrueDepth iPhone using the owner's Xcode signing account.
- [ ] Check synchronized RGB/depth alignment and thresholds across phones, lighting, and faces.
- [ ] Compare false acceptance/rejection rates for laptop-only and combined enrollment before claiming improved accuracy.
- [ ] Evaluate trained depth anti-spoofing and a 3D identity matcher if depth-based verification is needed. A laptop RGB camera cannot perform 3D matching by itself.

## Sensor decision

Use front TrueDepth for face capture. Rear LiDAR scene depth is a different sensor; this first implementation does not use it. The companion captures new sensor data, not Apple's private Face ID templates. Depth measurements are experimental capture-quality checks, not proven liveness or identity evidence. A transferred file is not hardware-attested.

Sources: [Apple: LiDAR capture](https://developer.apple.com/documentation/avfoundation/capturing-depth-using-the-lidar-camera), [Apple: synchronized video and depth](https://developer.apple.com/documentation/avfoundation/avcapturedataoutputsynchronizer), [Apple: TrueDepth capture](https://developer.apple.com/documentation/avfoundation/streaming-depth-data-from-the-truedepth-camera), [Apple: Face ID data](https://support.apple.com/guide/security/facial-matching-security-sece151358d1/web).

## Live tracking and capture fix

- [x] Add continuous local detection and a 478-landmark facial mesh using a worker.
- [x] Show exact face-quality status before and during capture, preserving geometry on quality warnings.
- [x] Normalize the blur check by face-crop size and keep recognition similarity thresholds unchanged.
- [x] Wait for stable frames, show sample progress and support cancellation before saving.
- [x] Add regression coverage for smooth clear frames, missing/small/multiple/blurred faces and live analysis responses: 64 tests pass.
- [x] Verify real mesh output (478 points), five-sample enrollment, recognition, cancellation, overlay toggle and camera shutdown in an isolated browser using a prerecorded test face. No saved user identities were changed.

## Attendance system

- [x] Add sessions with a snapshot of enrolled people, persistent check-in timestamps and saved history.
- [x] Mark present after three consecutive server-confirmed camera matches; keep present after leaving the frame.
- [x] Mark people not seen as absent when the session ends; retain manual corrections.
- [x] Add a live roster, status menus, session history and CSV export to the simple UI.
- [x] Preserve attendance history when an enrollment is removed.
- [x] Test confirmation/reset rules, separate scanners, duplicate sightings, roster snapshots, persistence, corrections and CSV safety: 80 tests pass.
- [x] Verify automatic check-in, absent status on session end and manual corrections in an isolated browser using a prerecorded test face.

## Parent portal

- [x] Add private guardian codes linked to one student, with read-only current attendance and recent history.
- [x] Show camera check-in timestamps and distinguish staff corrections.
- [x] Add staff controls to create and revoke parent access; show codes only at creation.
- [x] Protect the staff dashboard, roster, enrollment and CSV routes with separate staff sign-in.
- [x] Hash parent codes/session tokens, expire sessions and invalidate revoked or deleted-student access.
- [x] Test privacy boundaries, updates, corrections, login limits, expiry and restart persistence: 89 tests pass.
- [x] Verify staff sign-in, link creation, parent check-in display, blocked staff access and revocation in an isolated browser with test students.
- [ ] Configure an HTTPS deployment if parents need access from their own devices; this app remains local.

## Multiple faces without MediaPipe

- [x] Replace the active MediaPipe camera worker with continuous local YuNet boxes and five-point landmarks.
- [x] Support up to eight independently recognized faces in attendance and recognize-anyone mode.
- [x] Track attendance confirmation by identity, reset missing/uncertain matches and reject duplicate-identity faces in the same frame.
- [x] Keep enrollment and selected-person verification restricted to one face.
- [x] Add per-face results and name labels while preserving manual corrections and parent attendance views.
- [x] Cover group quality, face-order changes, missing faces, unknown faces, duplicate identities, session boundaries and request protection: 99 tests pass.
- [x] Verify two real test faces are recognized and checked in together in the browser, with ten local landmarks and separate name labels; verify group enrollment remains disabled. Saved user records were not changed.
