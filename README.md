# Identiq

A local laptop-camera attendance and face recognition app. Register students and staff, enroll five face samples against their school record, then scan to record arrivals and departures. Names, face patterns and attendance history are stored in SQLite on this laptop.

## Start

From this folder:

```sh
./run.sh
```

On macOS, you can also double-click **Start Identiq.command**. Open **http://localhost:8080** in Chrome, Edge, Firefox, or Safari. Sign in with the initial login ID and password in `data/staff-login.json` (created automatically and readable only by the laptop owner). Use **Staff login settings** to choose your own credentials; saving signs out every staff session and removes the initial credential file. Click **Enable camera** and allow camera access. If camera access is unavailable in the Codex preview, open that address in your regular browser.

The first run creates `.venv`, installs the Python dependencies, and downloads the YuNet detector plus the approximately 166 MB ArcFace model. Later runs work offline. Python 3.11 or later is required; this project was tested on Python 3.12. To use another port: `PORT=8081 ./run.sh`.

## Use

1. Enable your camera. Use even lighting and keep faces large enough to see clearly. Enrollment requires one person; attendance supports up to 20 together.
2. Choose **School directory → Add school record**. Students need admission number, full name, grade, section and academic year. Staff need employee code and full name. These records are saved before face capture. Choose **Enroll face** on their record, confirm permission and select **Capture & enroll**. Hold still for the first samples and gently turn your head for the later ones.
3. Select **Attendance**, name the school day, choose students, staff or everyone, and optionally filter students by academic year and class. Click **Start session**. Registered people are included even before face enrollment, so staff can record their attendance manually. The roster saves school details at that moment; later profile edits do not change historical records.
4. Choose **Automatic walking direction**, **Camera check-in** or **Camera check-out**, then start the scan. Automatic mode requires an enrolled face to cross the visible center line; configure the line and check-in direction using the mirrored preview. Camera modes require three fresh, strong matches of the same clear face over at least 0.6 seconds before recording a movement. Up to 20 enrolled faces are handled together. Disappearing from view does not record a departure.
5. Use a student’s **Check in / Check out** buttons for staff-recorded movements with a required note. Check-out requires a recorded arrival; repeated arrivals or departures do not duplicate events. Re-entry is supported, and the movement history keeps previous visits. **End session → End & save** marks pending students absent and flags check-ins without departure; it does not fabricate check-out times. Review history, correct attendance and export timestamps, campus state, student ID and class as CSV.

An active session survives a page reload or server restart; enable the camera and restart scanning to continue it. New enrollments join the next session. Manual corrections are preserved during later scans; choose **Not seen yet** in an active session to let the camera mark that person again. Absent means no check-in was recorded during the session, not proof that someone was physically absent.

The **Recognize** tab still retrieves names or verifies a selected identity without changing attendance. **Pause attendance scan** stops matching. **Turn off camera** releases the camera. **Remove** deletes that person's enrollment and face patterns; saved attendance retains their name and status.

If access was denied, enable camera permission for localhost in your browser's site settings. On macOS, check **System Settings → Privacy & Security → Camera** for that browser. If the camera is busy, close another app using it.

Use **Delete all attendance** beside Export CSV in the staff Attendance section to clear every session and attendance record, including attendance stored in local database backups. Confirm in the dialog. Enrolled people, face patterns and parent access stay intact. The button is disabled when attendance history is empty.

## Parent portal

Open **http://localhost:8080/parents** or use **Parent portal** in the header. Parents sign in with their login ID and password to see only their linked student's current or latest attendance, first camera check-in, last camera sighting and up to 100 recent sessions. The attendance calendar lets parents browse months and select a date to see every session, including mixed statuses on the same day. Empty dates mean no recorded session, not absence. Monthly calendar results include the full month, independently of the 100-session recent-history limit. Dates and times use Asia/Dubai by default; set ATTENDANCE_TIMEZONE to another IANA timezone before starting the server if needed. The page refreshes every 10 seconds while visible. Camera check-ins and staff corrections are labeled separately. A check-in is an attendance record, not a guarantee that the student is still on site.

From the staff dashboard, use **Parent access** below the people list: enter the verified guardian's name, select their student, choose a unique login ID and a password of 10–128 characters, then select **Create parent account**. Share those details privately; the app does not send messages or show saved passwords. Multiple guardians can have separate accounts for the same student. **Reset login** changes their credentials and signs out existing sessions. **Revoke access** disables their account. Removing the student's enrollment also invalidates linked parent access. Existing accounts created using codes retain their student links but display **Password required** until staff assign credentials. Old access-code logins are no longer accepted.

Staff routes require a separate staff session, including roster APIs and CSV exports. Passwords use salted scrypt hashes; session tokens use SHA-256 hashes. Login IDs are case-insensitive; passwords preserve case and spaces. Sessions expire after 12 hours, use HttpOnly/SameSite cookies and persist across server restarts. Login attempts are limited. Changing credentials invalidates the affected sessions. Sign out of the staff area before letting another person use the same browser. The initial staff credential file is never served by the app. If the staff password is forgotten, the laptop owner can run `.venv/bin/python scripts/reset_staff_login.py` to set a new ID and password using hidden terminal input.

**This remains a local prototype:** localhost on a parent’s phone refers to their phone, not this laptop. Use the configured Wi-Fi address for devices on that network. Internet access requires a separately configured HTTPS deployment. Verify guardian relationships before creating parent accounts.

## Live face tracking and groups

After **Enable camera**, the existing local YuNet detector tracks faces continuously and draws each face's box and five measured landmarks (eyes, nose and mouth corners). Toggle **Facial landmarks** to hide those points. MediaPipe is not used by the camera flow; its former worker has been removed. No dense face mesh is generated.

Attendance and **Recognize anyone enrolled** support up to 20 faces per frame. Every usable face is aligned and matched independently using the ArcFace `w600k_r50` model from InsightFace `buffalo_l`, with the existing quality and similarity thresholds. Each face has its own result and name label. Blurry, small, unknown or uncertain faces do not block other usable faces. More than 20 detected faces stops matching for that frame and asks you to split the group.

Attendance requires three distinct camera frames of the same identity, spaced at least 0.25 seconds apart and spanning at least 0.6 seconds. Confirmation is scoped to the session and scanner and follows the face location independently of detector order. A missing/uncertain identity, a jump to another location, a mode change or a gap longer than 1.5 seconds starts confirmation again. While the first recognition request runs, the camera keeps up to five later frames only in memory and processes them in order; this lets a person walking past receive a provisional name while confirmation finishes. A provisional name never marks attendance. Frames are discarded when scanning stops and are never saved. Frames that take more than three seconds to process, repeated decoded images and out-of-order responses cannot mark attendance. Faces must be at least 96 pixels across, with sharpness at least 12 and detector score at least 0.85. Recognition outside attendance still uses three matches to stabilize the displayed name. If two faces match the same enrolled identity in one frame, neither contributes to that identity's check-in. Manual corrections, saved history and parent views continue to use the same attendance records.

**Enrollment and selected-person verification still require exactly one face.** Enrollment waits for two ready frames and collects five accepted samples at least 800 ms apart. Quality checks normalize the face crop to 128×128. Missing, small, multiple and very soft faces show their specific reason; capture can be cancelled before saving.

All detection and recognition happens on this laptop. Facial landmarks are estimated from RGB and are not a depth or liveness test. Camera frames remain in memory.

## iPhone enrollment

Choose **Enroll → iPhone scan** to import a capture exported by the native companion in [`ios/`](ios/README.md). It uses the **front TrueDepth camera**, rather than the rear LiDAR scanner. Capture five guided poses on the phone, share the JSON file via AirDrop or Files, and import it on the laptop. You can enroll a new person or add phone samples to an existing person after confirming that the faces match.

The companion source and Xcode project are provided, but a native build is currently blocked by the unaccepted Xcode license. Swift syntax and project-file structure were checked; hardware behavior has not been verified. Installation requires your Apple development team and a physical TrueDepth iPhone. See [`TODO.md`](TODO.md) for completed work and remaining device validation.

Depth measurements are experimental capture-quality checks; they are not used by the ArcFace identity matcher. Better accuracy and anti-spoofing are not established. Ordinary laptop verification still uses RGB photos. Imported files contain photos and should be deleted after import; the laptop database retains embeddings and sanitized capture/source metadata, not those photos.

## Storage and matching

Names, 512-value ArcFace embeddings, session rosters, attendance status and check-in timestamps are stored in `data/identities.sqlite3`. Camera images are processed in memory and are not saved. The database directory has owner-only permissions; the database is readable and writable by its owner. **Embeddings are biometric data and the database is not encrypted.** The app listens only on `127.0.0.1`; staff sign-in is required to manage its directory. People with access to the laptop owner's files can still read the database and initial staff credential file.

The staff page’s **Database transfer** section downloads a consistent SQLite backup and merges one from another laptop. If a device signs in to this server’s shared address, its changes already go directly into this server’s database and no transfer is needed. To move data from a laptop that used its own local server, download a backup there, then upload it to the destination server. Profiles with matching admission numbers or employee codes are joined; face samples and new attendance sessions/records are added. Re-uploading a backup is safe and later backups from that same laptop add newly saved samples and attendance. Imported active sessions become closed; attendance edits to records already imported are not synchronized. Parent accounts are copied when their login ID is free; colliding parent accounts are skipped. Staff credentials and login sessions are never imported. Backups include face embeddings and parent password hashes, so transfer them only through the school’s trusted network and delete the download when finished.

YuNet detects one face and five landmarks; ArcFace aligns the face and computes its embedding. Five normalized embeddings are saved per enrollment. Matching averages cosine similarity across a person's samples. Recognition requires a similarity of at least **0.65** and a lead of at least **0.12** over the next candidate across the entire enrolled directory. Scores are similarities, not probabilities. The 0.65 value is a temporary, more conservative cutoff; it is not calibrated against the school's cameras and can increase rejected matches. Calibrate with consented genuine and impostor captures before relying on it for attendance. Recognition and attendance safeguards live in `attendance_policy.py`; enrollment duplicate checks retain their separate 0.45 threshold and 0.06 ambiguity margin. Duplicate enrollments and inconsistent enrollment samples are rejected.

The public `buffalo_l` weights are restricted to non-commercial academic research by InsightFace. This installation must remain within that scope. Switching from SFace changes embeddings from 128 to 512 values, so existing SFace samples are kept for record preservation but are not matched. Re-enroll each person before using ArcFace attendance.

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
- [InsightFace model licensing](https://github.com/deepinsight/insightface/blob/master/README.md)

`scripts/download_models.py` verifies YuNet against the official model Git LFS SHA-256 checksum and downloads `w600k_r50.onnx` from InsightFace's official `buffalo_l` release archive. YuNet's model is distributed under the MIT license. The public ArcFace weights are restricted to non-commercial academic research by their publisher.

## School movement workflow

The parent portal shows the latest arrival/departure, student ID/class and movement history on its calendar. It exposes only the linked student and omits staff notes. Attendance remains Present after departure. Legacy Present records migrate to “location not recorded” because old camera sightings cannot establish arrival or departure.

Automatic direction tracking uses enrolled identities and normalized face centers across a dead zone around the center line. It resets after a missed or uncertain recognition or a 1.5-second gap. The camera must clearly see the same enrolled face on both sides; fast passages, turns and missed frames may need a staff correction. Arrival/departure and source are recorded for each movement, and changing modes pauses the scan. This is a tested local workflow, not a validated physical gate or release authorization. Real school deployment still needs individual staff accounts, encrypted storage, backups/retention policies and validated liveness/accuracy testing.

## Run on the local network

Run `./run-network.sh` while the laptop is connected to Wi-Fi. It discovers the laptop’s Wi-Fi IPv4 address and serves HTTPS on port 8443, separately from the normal localhost server. To choose an address or port, use `./run-network.sh --address 192.168.1.20 --port 8443`. Only the selected private LAN interface is used; staff and parent authentication and same-origin request checks remain enabled.

Open the printed staff URL or `/parents` on a device connected to the same network. The server creates a local certificate in `data/tls/lan-ADDRESS.crt`; trust that public certificate on each device before using the app or camera. Keep the `.key` file private on this laptop. No certificate trust settings are changed automatically. A new laptop IP generates a different certificate and URL. The laptop must remain awake, and Wi-Fi client isolation or a firewall can prevent connections from other devices. This command does not publish the app on the internet.

## Shared camera and HTTP Wi-Fi access

Use `./run-network.sh --address YOUR-LAN-IP --http --port 8080` to run one shared server on both that LAN IP and `http://localhost:8080`. On the laptop, sign in at localhost, enable the camera, and click **Share this camera**. On another device, sign in at `http://YOUR-LAN-IP:8080`; it receives the camera preview and can start, pause or configure the shared scan. Only the camera source submits recognition frames. All connected staff tabs receive movement updates. Parents have no access to the feed or controls. The source tab must remain open; losing frames for five seconds pauses writes and clears the shared feed. While the camera page remains open, it reconnects using its private source key and resumes the same attendance session once fresh frames return. Manual pauses, mode changes, camera shutdown and session end cancel automatic scanning. The source has a 30-second recovery window before another staff camera may claim the feed.

Controls and a small latest-frame JPEG preview use authenticated same-origin WebSockets. There is one in-memory source and no saved camera video. Preview frames are capped at about five per second and slow viewers receive the latest frame, rather than an unbounded queue. This shared state belongs to one server process; run the LAN and localhost listeners with the command above, rather than starting separate servers. The ordinary attendance polling remains a fallback.

HTTP does not encrypt passwords, sessions or the feed. Browser camera capture is unavailable on a plain HTTP LAN IP; use the localhost source page on the laptop. HTTP mode uses separate cookie names to avoid collisions with previously issued HTTPS cookies. Login credentials and permissions are unchanged. HTTPS mode remains available by omitting `--http`.

## Use a tablet as the camera source

Run `./run-network.sh --address YOUR-LAN-IP --http --port 8080 --https-port 8443`. The HTTP and HTTPS listeners belong to one app and share the camera, controls and attendance state. On the tablet, download the public certificate from `http://YOUR-LAN-IP:8080/network-certificate`, then install/trust it as a CA certificate using the tablet's security settings. Open `https://YOUR-LAN-IP:8443/`, sign in as staff, enable the device camera and choose **Share this camera**. Other staff tabs can view and control it using the HTTP address. If another device is sharing, stop sharing on that source before switching cameras. HTTPS and HTTP use separate session-cookie names so their sessions do not conflict. The public certificate download never serves the private key. Browser certificate trust and camera permission must be completed by the device's user.

## Distant faces

The camera requests up to 1920×1080 when the device supports it. Attendance frames are compressed to 800 pixels on their longest side at 72% JPEG quality, with only the newest queued confirmation frame retained, to reduce Wi-Fi transfer, server decoding and stale-frame delay. Live face-landmark analysis uses a separate 640-pixel frame, at lower JPEG quality outside enrollment, so it can update without delaying recognition. Group detection uses a 960-pixel pass plus a 1280-pixel pass when faces are small or missing. Detections from both passes are merged to avoid duplicate face results. Group recognition accepts clear faces with at least 64 pixels on their shorter side without requiring them to occupy 10% of the scene. Enrollment and selected-person verification retain the closer-face requirement. Recognition now requires a 0.65 similarity and 0.12 ambiguity margin. Distance still depends on the camera’s actual resolution, focus and lighting; this does not establish long-distance recognition accuracy.

The HTTP network command now also starts the tablet HTTPS listener on port 8443 by default. Use `--https-port` to choose a different HTTPS port. **Fullscreen camera** requests native browser fullscreen from the button tap, with an expanded-page fallback where the browser does not support it. Esc, the system exit gesture and the Exit fullscreen button restore the normal layout. Fullscreen face labels and attendance names use larger type for tablet viewing.

## connection recovery

The camera page shows one connection notice instead of repeated network-error popups. Requests have a deadline; scanning and WebSockets retry with bounded exponential backoff. Old scan results are rejected after the source or session changes. A server restart refreshes the page’s request token without requiring a reload; an expired staff login still requires sign-in. No camera images are stored or queued for later attendance replay. Attendance cannot be recorded during an outage.

Empty scenes use fewer recognition requests, previews upload less often without viewers, and connected staff tabs receive attendance changes over WebSockets instead of redundant polling. Starting scanning requests a screen wake lock where supported. Keep the designated camera page open and the device unlocked: a closed browser, locked device, suspended OS or expired login still needs attention.

The network launcher restarts an unexpected server exit using the original IP address. Ctrl+C stops it. Startup failures are limited to three attempts so an invalid address or occupied port does not loop forever. A stalled HTTPS handshake times out in its own worker and cannot block other devices. When the laptop’s Wi-Fi address changes, restart with the new approved address and trust its new public certificate on the tablet.

## after pulling an update

`git pull` updates files, but an already running server keeps its loaded Python code. Stop the existing server with Ctrl+C, start it again using the same command and refresh the tablet page. JavaScript and CSS now carry an app version in their URLs so refreshed pages request the matching assets.

Choose **Connection check** on the sign-in page, parent portal or camera panel. It shows the tablet address, page version, running server version, installed version, reachability and the last failed request. It distinguishes a server that needs restarting from a tablet page that needs refreshing. Request bodies, credentials, student records and images are excluded. All pages use bounded request deadlines and readable network errors; parent attendance reads reconnect automatically. Enrollment, login and other mutations are never automatically replayed after a transport failure.

## school static address and enrollment filters

For the school laptop whose existing static address is `10.10.40.40`, stop the old server, pull updates, then run:

```sh
python scripts/run_network.py --address 10.10.40.40 --http --port 8080 --https-port 8443
```

Open **https://10.10.40.40:8443/** on the tablet for its camera. Plain HTTP is **http://10.10.40.40:8080/**. The startup command remembers an explicitly selected address in the ignored local `data/network.json`; later launches use that address. This only binds the app to an address already assigned to the laptop. It does not configure Windows networking. Use `--auto-address` when intentionally moving the app to another network. A saved address never silently falls back to a different interface.

IPs preceding timestamped request logs identify clients. They do not indicate that the server IP changed. Startup prints the server's bind address and protocol-specific links. A TLS-looking binary request logged as HTTP 400 can mean HTTPS was sent to the HTTP port. `ERR_CONNECTION_REFUSED` on port 8443 means the HTTPS service at that address is not accepting the connection; working internet access does not verify local server access. If it persists after startup, check that 10.10.40.40 is actually assigned to the laptop and ask school IT to check inbound TCP 8443 and routing between the tablet and server networks. Do not disable the firewall.

Both `run_network.py` and `watch_network.py` now supervise unexpected exits. All advertised listeners are monitored together; if HTTPS stops, the whole network worker restarts instead of silently leaving only HTTP running. Ctrl+C stops the service normally.

In **Enroll**, choose **Grade** and **Section** before selecting a student. Name/admission-number search is also available. These filters apply to laptop capture and iPhone import; changing the filters clears a selected record that no longer matches. Existing students without grade metadata and staff remain available under **Staff / no grade assigned**. Face-save requests allow up to 60 seconds for upload and processing, with redundant live analysis paused during the save. A timed-out enrollment is not automatically resubmitted; check that student's record before capturing again.

Group attendance now permits up to 20 faces in one frame. This is a processing limit, not a guarantee of real-world detection speed or distance. The stricter recognition and attendance safeguards apply independently to every face; enrollment still needs one face at a time. Larger groups require enough camera detail per face and more laptop processing time.

## Incorrect attendance or names

A single face match no longer writes attendance. Similarity, the runner-up margin, image quality, face continuity and fresh-frame confirmation are enforced on the server for both single-face and group camera routes, including walking-direction mode. Staff movement notes include the number of confirmation frames and their lowest match similarity. Manual corrections remain authoritative.

Existing attendance is not retroactively changed: the app cannot determine which past matches were wrong. Staff should correct the affected records in the roster. A face pattern enrolled under the wrong student's name must be removed and enrolled under the correct record. Photos, videos and consistently mistaken matches remain possible without validated liveness and deployment-specific accuracy evaluation; use staff verification for disputed or uncertain cases. Restart the server after pulling these updates, then reload the tablet page.

## Scan efficiency and diagnostics

Attendance checks face quality before embedding extraction, scores the full directory while retaining just the exact best two candidates, and loads each group's roster records with one database query. The camera follows the server's confirmation delay instead of repeatedly sending frames too early to count. Existing match thresholds and confirmation requirements still apply. Last-seen refreshes are limited to once every five seconds per checked-in record; actual arrival and departure timestamps are saved immediately.

After a scan, open **Connection check** to compare **Scan request round trip** with **Scan server processing**. These refer to the last successful camera request, with its time and face count shown. The separate **Server response** row measures the connection check itself. Request round trip includes upload, server handling, network transit and browser response handling; server scan processing covers the recognition route, not pure model inference. Diagnostics do not retain names, camera frames or face patterns.

See `PERFORMANCE.md` for the synthetic matching benchmark and its limits. Pull the changes, restart the server and refresh the tablet page to load the updated scan scheduler.

## Weak Wi-Fi and moving between floors

Styling is delivered inside each HTML page, with an exact CSP hash, so it does not depend on a second CSS request completing. Pages and records remain `no-store`. Public first-party JavaScript/CSS files are precompressed and cached using their build-version URLs, with encoding-specific ETags; unversioned or mismatched URLs must revalidate. File bytes remain frozen for the running build until the server restarts. There is no offline cache of student records, credentials, camera frames or attendance writes.

Shared preview now uses a smaller 480-pixel image at up to two frames per second. On a slow recent scan request, a reported low-bandwidth connection or Data Saver, it uses 360 pixels and at most one frame per second. A backed-up socket drops preview captures rather than continually queuing them. This changes the remote viewing preview; recognition requests keep their existing image resolution and JPEG quality, and all attendance safeguards still apply.

**Connection check** makes five small requests, reports how many succeeded and the slowest successful response, and stops when the dialog closes. Follow-up requests have a 2.5-second deadline. These are HTTP reachability samples, not a Wi-Fi signal measurement or proof of continuous connectivity. Compare results downstairs and upstairs with the camera page kept open.

If the entire app becomes unreachable only upstairs, school IT needs to verify upstairs access-point coverage, roaming and routing/firewall access to the attendance laptop's configured IP and port 8443 (and 8080 if HTTP viewing is used). Keep the laptop awake and the server running. An internet connection alone does not establish access to the private attendance server. Caching and smaller previews reduce app traffic; they cannot repair a missing network route or coverage gap. Live recognition cannot mark attendance while the laptop is unreachable.
