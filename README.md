# Identiq

A local laptop-camera attendance and face recognition app. Enroll someone's name and five face samples, then scan them to mark attendance. Names, face patterns and attendance history are stored in SQLite on this laptop.

## Start

From this folder:

```sh
./run.sh
```

On macOS, you can also double-click **Start Identiq.command**. Open **http://localhost:8080** in Chrome, Edge, Firefox, or Safari. Sign in with the initial login ID and password in `data/staff-login.json` (created automatically and readable only by the laptop owner). Use **Staff login settings** to choose your own credentials; saving signs out every staff session and removes the initial credential file. Click **Enable camera** and allow camera access. If camera access is unavailable in the Codex preview, open that address in your regular browser.

The first run creates `.venv`, installs the Python dependencies, and downloads about 39 MB of official OpenCV models. Later runs work offline. Python 3.11 or later is required; this project was tested on Python 3.12. To use another port: `PORT=8081 ./run.sh`.

## Use

1. Enable your camera. Use even lighting and keep faces large enough to see clearly. Enrollment requires one person; attendance supports up to eight together.
2. Select **Enroll → Laptop camera**, enter their name, and confirm their permission. Click **Capture & enroll**. Hold still for the first samples and gently turn your head for the later ones.
3. Set student IDs and classes using **Student details** in the directory. Select **Attendance**, name the school day, choose all students or one class and click **Start session**. The roster saves its student details at that moment.
4. Choose **Automatic walking direction**, **Camera check-in** or **Camera check-out**, then start the scan. Automatic mode requires an enrolled face to cross the visible center line; configure the line and check-in direction using the mirrored preview. Camera modes record a movement on one successful match. Up to eight enrolled faces are handled together. Disappearing from view does not record a departure.
5. Use a student’s **Check in / Check out** buttons for staff-recorded movements with a required note. Check-out requires a recorded arrival; repeated arrivals or departures do not duplicate events. Re-entry is supported, and the movement history keeps previous visits. **End session → End & save** marks pending students absent and flags check-ins without departure; it does not fabricate check-out times. Review history, correct attendance and export timestamps, campus state, student ID and class as CSV.

An active session survives a page reload or server restart; enable the camera and restart scanning to continue it. New enrollments join the next session. Manual corrections are preserved during later scans; choose **Not seen yet** in an active session to let the camera mark that person again. Absent means no check-in was recorded during the session, not proof that someone was physically absent.

The **Recognize** tab still retrieves names or verifies a selected identity without changing attendance. **Pause attendance scan** stops matching. **Turn off camera** releases the camera. **Remove** deletes that person's enrollment and face patterns; saved attendance retains their name and status.

If access was denied, enable camera permission for localhost in your browser's site settings. On macOS, check **System Settings → Privacy & Security → Camera** for that browser. If the camera is busy, close another app using it.

Use **Delete all attendance** beside Export CSV in the staff Attendance section to clear every session and attendance record, including attendance stored in local database backups. Confirm in the dialog. Enrolled people, face patterns and parent access stay intact. The button is disabled when attendance history is empty.

## Parent portal

Open **http://localhost:8080/parents** or use **Parent portal** in the header. Parents sign in with their login ID and password to see only their linked student's current or latest attendance, first camera check-in, last camera sighting and up to 100 recent sessions. The attendance calendar lets parents browse months and select a date to see every session, including mixed statuses on the same day. Empty dates mean no recorded session, not absence. Monthly calendar results include the full month, independently of the 100-session recent-history limit. Dates and times use Asia/Dubai by default; set ATTENDANCE_TIMEZONE to another IANA timezone before starting the server if needed. The page refreshes every 10 seconds while visible. Camera check-ins and staff corrections are labeled separately. A check-in is an attendance record, not a guarantee that the student is still on site.

From the staff dashboard, use **Parent access** below the people list: enter the verified guardian's name, select their student, choose a unique login ID and a password of 10–128 characters, then select **Create parent account**. Share those details privately; the app does not send messages or show saved passwords. Multiple guardians can have separate accounts for the same student. **Reset login** changes their credentials and signs out existing sessions. **Revoke access** disables their account. Removing the student's enrollment also invalidates linked parent access. Existing accounts created using codes retain their student links but display **Password required** until staff assign credentials. Old access-code logins are no longer accepted.

Staff routes require a separate staff session, including roster APIs and CSV exports. Passwords use salted scrypt hashes; session tokens use SHA-256 hashes. Login IDs are case-insensitive; passwords preserve case and spaces. Sessions expire after 12 hours, use HttpOnly/SameSite cookies and persist across server restarts. Login attempts are limited. Changing credentials invalidates the affected sessions. Sign out of the staff area before letting another person use the same browser. The initial staff credential file is never served by the app. If the staff password is forgotten, the laptop owner can run `.venv/bin/python scripts/reset_staff_login.py` to set a new ID and password using hidden terminal input.

**This remains a local prototype:** localhost on a parent's phone refers to their phone, not this laptop. Remote parent access requires a separately configured HTTPS deployment and a reachable server. The current server still accepts only localhost and has not been exposed publicly. Portal codes grant attendance access, so verify guardian relationships before issuing them.

## Live face tracking and groups

After **Enable camera**, the existing local YuNet detector tracks faces continuously and draws each face's box and five measured landmarks (eyes, nose and mouth corners). Toggle **Facial landmarks** to hide those points. MediaPipe is not used by the camera flow; its former worker has been removed. No dense face mesh is generated.

Attendance and **Recognize anyone enrolled** support up to eight faces per frame. Every usable face is aligned and matched independently using SFace, with the existing quality and similarity thresholds. Each face has its own result and name label. Blurry, small, unknown or uncertain faces do not block other usable faces. More than eight detected faces stops matching for that frame and asks you to split the group.

Attendance uses one successful match per identity, independent of detector order, position or scanner. Recognition outside attendance still uses three matches to stabilize the displayed name. If two faces match the same enrolled identity in one frame, neither contributes to that identity's check-in. Manual corrections, saved history and parent views continue to use the same attendance records.

**Enrollment and selected-person verification still require exactly one face.** Enrollment waits for two ready frames and collects five accepted samples at least 800 ms apart. Quality checks normalize the face crop to 128×128. Missing, small, multiple and very soft faces show their specific reason; capture can be cancelled before saving.

All detection and recognition happens on this laptop. Facial landmarks are estimated from RGB and are not a depth or liveness test. Camera frames remain in memory.

## iPhone enrollment

Choose **Enroll → iPhone scan** to import a capture exported by the native companion in [`ios/`](ios/README.md). It uses the **front TrueDepth camera**, rather than the rear LiDAR scanner. Capture five guided poses on the phone, share the JSON file via AirDrop or Files, and import it on the laptop. You can enroll a new person or add phone samples to an existing person after confirming that the faces match.

The companion source and Xcode project are provided, but a native build is currently blocked by the unaccepted Xcode license. Swift syntax and project-file structure were checked; hardware behavior has not been verified. Installation requires your Apple development team and a physical TrueDepth iPhone. See [`TODO.md`](TODO.md) for completed work and remaining device validation.

Depth measurements are experimental capture-quality checks; they are not used by the SFace identity matcher. Better accuracy and anti-spoofing are not established. Ordinary laptop verification still uses RGB photos. Imported files contain photos and should be deleted after import; the laptop database retains embeddings and sanitized capture/source metadata, not those photos.

## Storage and matching

Names, 128-value SFace embeddings, session rosters, attendance status and check-in timestamps are stored in `data/identities.sqlite3`. Camera images are processed in memory and are not saved. The database directory has owner-only permissions; the database is readable and writable by its owner. **Embeddings are biometric data and the database is not encrypted.** The app listens only on `127.0.0.1`; staff sign-in is required to manage its directory. People with access to the laptop owner's files can still read the database and initial staff credential file.

YuNet detects one face and five landmarks; SFace aligns the face and computes its embedding. Five normalized embeddings are saved per enrollment. Matching averages cosine similarity across a person's samples. The default threshold is **0.45** and the winner must lead the next candidate by at least **0.06**. Scores are similarities, not probabilities. Adjust `THRESHOLD` and `MARGIN` in `biometrics.py` only after testing representative matching and nonmatching faces for your use case. Duplicate enrollments and inconsistent enrollment samples are rejected.

This is a functional recognition prototype. **It does not perform liveness detection: a photo or replayed video may match.** Three consecutive matches help stabilize the display; they do not establish liveness. Lighting, pose, camera quality, and appearance changes affect results. Use a separate authentication factor before relying on this for access control. SQLite deletion removes active records, but is not a guarantee of forensic erasure or deletion from backups.

## Development

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

If your environment was created with `uv`, use `uv pip install --python .venv/bin/python -r requirements-dev.txt` instead of the pip command.

`parent_portal.py` implements scoped guardian access and staff/parent sessions. `attendance.py` implements roster snapshots, check-in confirmation, history and CSV export. `app.py` provides the Flask routes; `biometrics.py` implements image validation, detection, embeddings, matching, and storage. `static/` and `templates/` contain the camera UI. The tests use controlled embeddings to check persistence, deletion, recognition, wrong claims, ambiguous matches, invalid images, consent, local request protection, attendance confirmation, session persistence, manual corrections and CSV exports without needing a webcam. The app starts with an empty identity directory.

## Model sources

- [OpenCV face detection and recognition tutorial](https://docs.opencv.org/4.13.0/d0/dd4/tutorial_dnn_face.html)
- [YuNet model and license](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
- [SFace model and license](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface)

`scripts/download_models.py` verifies downloaded files against the official model Git LFS SHA-256 checksums. YuNet's model is distributed under the MIT license; SFace's model is distributed under the Apache 2.0 license. License copies are included in `models/`. The models remain the work of their respective authors.

## School movement workflow

The parent portal shows the latest arrival/departure, student ID/class and movement history on its calendar. It exposes only the linked student and omits staff notes. Attendance remains Present after departure. Legacy Present records migrate to “location not recorded” because old camera sightings cannot establish arrival or departure.

Automatic direction tracking uses enrolled identities and normalized face centers across a dead zone around the center line. It resets after a missed or uncertain recognition or a 1.5-second gap. The camera must clearly see the same enrolled face on both sides; fast passages, turns and missed frames may need a staff correction. Arrival/departure and source are recorded for each movement, and changing modes pauses the scan. This is a tested local workflow, not a validated physical gate or release authorization. Real school deployment still needs individual staff accounts, encrypted storage, backups/retention policies and validated liveness/accuracy testing.

## Run on the local network

Run `./run-network.sh` while the laptop is connected to Wi-Fi. It discovers the laptop’s Wi-Fi IPv4 address and serves HTTPS on port 8443, separately from the normal localhost server. To choose an address or port, use `./run-network.sh --address 192.168.1.20 --port 8443`. Only the selected private LAN interface is used; staff and parent authentication and same-origin request checks remain enabled.

Open the printed staff URL or `/parents` on a device connected to the same network. The server creates a local certificate in `data/tls/lan-ADDRESS.crt`; trust that public certificate on each device before using the app or camera. Keep the `.key` file private on this laptop. No certificate trust settings are changed automatically. A new laptop IP generates a different certificate and URL. The laptop must remain awake, and Wi-Fi client isolation or a firewall can prevent connections from other devices. This command does not publish the app on the internet.

## Shared camera and HTTP Wi-Fi access

Use `./run-network.sh --address YOUR-LAN-IP --http --port 8080` to run one shared server on both that LAN IP and `http://localhost:8080`. On the laptop, sign in at localhost, enable the camera, and click **Share this camera**. On another device, sign in at `http://YOUR-LAN-IP:8080`; it receives the camera preview and can start, pause or configure the shared scan. Only the camera source submits recognition frames. All connected staff tabs receive movement updates. Parents have no access to the feed or controls. The source tab must remain open; disconnecting it or losing frames for five seconds pauses the scan and clears the shared feed. Reconnecting does not silently reclaim the camera.

Controls and a small latest-frame JPEG preview use authenticated same-origin WebSockets. There is one in-memory source and no saved camera video. Preview frames are capped at about five per second and slow viewers receive the latest frame, rather than an unbounded queue. This shared state belongs to one server process; run the LAN and localhost listeners with the command above, rather than starting separate servers. The ordinary attendance polling remains a fallback.

HTTP does not encrypt passwords, sessions or the feed. Browser camera capture is unavailable on a plain HTTP LAN IP; use the localhost source page on the laptop. HTTP mode uses separate cookie names to avoid collisions with previously issued HTTPS cookies. Login credentials and permissions are unchanged. HTTPS mode remains available by omitting `--http`.

## Use a tablet as the camera source

Run `./run-network.sh --address YOUR-LAN-IP --http --port 8080 --https-port 8443`. The HTTP and HTTPS listeners belong to one app and share the camera, controls and attendance state. On the tablet, download the public certificate from `http://YOUR-LAN-IP:8080/network-certificate`, then install/trust it as a CA certificate using the tablet's security settings. Open `https://YOUR-LAN-IP:8443/`, sign in as staff, enable the device camera and choose **Share this camera**. Other staff tabs can view and control it using the HTTP address. If another device is sharing, stop sharing on that source before switching cameras. HTTPS and HTTP use separate session-cookie names so their sessions do not conflict. The public certificate download never serves the private key. Browser certificate trust and camera permission must be completed by the device's user.

## Distant faces

The camera requests up to 1920×1080 when the device supports it. Attendance frames preserve up to 1600 pixels on their longest side, and group detection uses a 960-pixel pass plus a 1280-pixel pass when faces are small or missing. Detections from both passes are merged to avoid duplicate face results. Group recognition accepts clear faces with at least 64 pixels on their shorter side without requiring them to occupy 10% of the scene. Enrollment and selected-person verification retain the closer-face requirement. Similarity and ambiguity thresholds are unchanged. Distance still depends on the camera’s actual resolution, focus and lighting; this does not establish long-distance recognition accuracy.

The HTTP network command now also starts the tablet HTTPS listener on port 8443 by default. Use `--https-port` to choose a different HTTPS port. **Fullscreen camera** requests native browser fullscreen from the button tap, with an expanded-page fallback where the browser does not support it. Esc, the system exit gesture and the Exit fullscreen button restore the normal layout. Fullscreen face labels and attendance names use larger type for tablet viewing.
