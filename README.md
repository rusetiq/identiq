# Identiq

A local laptop-camera attendance and face recognition app. Enroll someone's name and five face samples, then scan them to mark attendance. Names, face patterns and attendance history are stored in SQLite on this laptop.

## Start

From this folder:

```sh
./run.sh
```

On macOS, you can also double-click **Start Identiq.command**. Open **http://localhost:8080** in Chrome, Edge, Firefox, or Safari. Sign in using the code in `data/staff-access-code.txt` (created automatically and readable only by the laptop owner). Click **Enable camera** and allow camera access. If camera access is unavailable in the Codex preview, open that address in your regular browser.

The first run creates `.venv`, installs the Python dependencies, and downloads about 39 MB of official OpenCV models. Later runs work offline. Python 3.11 or later is required; this project was tested on Python 3.12. To use another port: `PORT=8081 ./run.sh`.

## Use

1. Enable your camera. Use even lighting and keep faces large enough to see clearly. Enrollment requires one person; attendance supports up to eight together.
2. Select **Enroll → Laptop camera**, enter their name, and confirm their permission. Click **Capture & enroll**. Hold still for the first samples and gently turn your head for the later ones.
3. Select **Attendance**, name the session and click **Start session**. Everyone enrolled at that moment starts as **Not seen yet**.
4. Click **Start attendance scan**. Up to eight people can check in together. One successful match for each identity marks them **Present**; repeated sightings update their last-seen time without adding duplicate rows. They stay present after leaving the camera view.
5. Click **End session → End & save**. People still not seen become **Absent**. Review saved sessions using **Session history**, correct any missed scan with a person's status menu, and download **Export CSV**.

An active session survives a page reload or server restart; enable the camera and restart scanning to continue it. New enrollments join the next session. Manual corrections are preserved during later scans; choose **Not seen yet** in an active session to let the camera mark that person again. Absent means no check-in was recorded during the session, not proof that someone was physically absent.

The **Recognize** tab still retrieves names or verifies a selected identity without changing attendance. **Pause attendance scan** stops matching. **Turn off camera** releases the camera. **Remove** deletes that person's enrollment and face patterns; saved attendance retains their name and status.

If access was denied, enable camera permission for localhost in your browser's site settings. On macOS, check **System Settings → Privacy & Security → Camera** for that browser. If the camera is busy, close another app using it.

## Parent portal

Open **http://localhost:8080/parents** or use **Parent portal** in the header. Parents sign in with a private access code to see only their linked student's current or latest attendance, first camera check-in, last camera sighting and up to 100 recent sessions. The attendance calendar lets parents browse months and select a date to see every session, including mixed statuses on the same day. Empty dates mean no recorded session, not absence. Monthly calendar results include the full month, independently of the 100-session recent-history limit. Dates and times use Asia/Dubai by default; set ATTENDANCE_TIMEZONE to another IANA timezone before starting the server if needed. The page refreshes every 10 seconds while visible. Camera check-ins and staff corrections are labeled separately. A check-in is an attendance record, not a guarantee that the student is still on site.

From the staff dashboard, use **Parent access** below the people list: enter the verified guardian's name, choose the enrolled student and select **Create access code**. The code is shown once. Save it and share it privately with that guardian; this app does not send messages. Multiple guardians can have separate codes for the same student. Select **Revoke access** to invalidate that code and its existing sessions; create a new code if needed. Removing the student's enrollment also invalidates linked parent access.

Staff routes now require a separate staff session, including roster APIs and CSV exports. The local staff code is in `data/staff-access-code.txt`; it is never served by the app. Parent codes and session tokens are stored as SHA-256 hashes in SQLite. Sessions expire after 12 hours, use HttpOnly/SameSite cookies, and persist across server restarts. Login attempts are limited. Sign out of the staff area before letting another person use the same browser.

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

Names, 128-value SFace embeddings, session rosters, attendance status and check-in timestamps are stored in `data/identities.sqlite3`. Camera images are processed in memory and are not saved. The database directory has owner-only permissions; the database is readable and writable by its owner. **Embeddings are biometric data and the database is not encrypted.** The app listens only on `127.0.0.1`; staff sign-in is required to manage its directory. People with access to the laptop owner's files can still read the database and local staff code.

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
