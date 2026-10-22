# Performance and reliability update

## 22 October 2026

A disposable benchmark with 1,000 synthetic identities, 5,000 embeddings and 20 queries per frame measured a median **3.289 ms** for the full candidate ranking versus **0.417 ms** for exact top-two selection across 40 alternating runs on the development laptop (7.89× for this matching stage). The top two IDs and scores were identical. Candidate objects allocated per frame fell from 20,000 to 40. This excludes camera capture, Wi-Fi, face detection and embedding inference; it is not an end-to-end speed or school-PC accuracy measurement.

Reproduce with `.venv/bin/python scripts/benchmark_matching.py --identities 1000 --faces 20 --runs 40`. It creates and removes its own synthetic database and does not read school records.

- Every identity is still scored. Recognition retains the exact global winner and runner-up without sorting/materializing the entire directory; selected-person verification and enrollment retain their full candidate checks.
- Attendance applies the existing quality cutoffs before running SFace. Small, blurred or weakly detected faces retain their adjustment messages and landmarks, without spending embedding/matching work on rejected faces. The same cutoffs remain enforced at the attendance write boundary.
- The server supplies the next useful scan delay. The tablet waits for confirmation timing instead of submitting bursts that cannot count, and empty or unusable scenes use a slower cadence. Requests remain sequential, with a bounded client fallback.
- A group loads its matching roster records with one query instead of one per face. Already checked-in students refresh last-seen at most once every five seconds; actual check-in/out timestamps and new movements are written immediately.
- Connection check now includes the last camera request round trip, server scan processing time, face count and sample time. The ordinary reachability check remains separate. Diagnostic samples retain only numbers and a timestamp, with no identity, image or face pattern.

The 0.60 similarity threshold, 0.12 runner-up margin, three-frame confirmation, minimum confirmation duration, continuity, stale-frame and manual-correction safeguards remain in force. Real tablet recognition accuracy still needs deployment-specific validation.

Validation for this update: 174 Python checks and 22 JavaScript checks passed, including tied candidate scores, selected-person claims, group query counts, early quality rejection, three-frame pacing, last-seen refreshes and diagnostic privacy. These use synthetic inputs and controlled inference, not a field accuracy benchmark.

## Earlier baseline from 8 October 2026

Measured locally on 8 October 2026 with 1,000 synthetic identities, 5,000 normalized face embeddings and eight queries per frame. The previous code took 46.17 ms (median of six runs). Batched cached matching takes 1.55 ms (median of 20 warm runs); first-load time is 10.26 ms. This measures database loading and candidate scoring, excluding camera capture, detection and embedding inference. It is not an overall FPS or accuracy claim.

- Recognition scores still average enrolled sample similarities. Recognition now requires similarity 0.60 and a 0.12 runner-up margin. Automatic attendance requires three fresh, clear, spatially continuous observations over at least 0.6 seconds, with at least 0.25 seconds between contributing frames. A single fast result cannot mark attendance. Requests remain sequential and use the existing scan cadence; the server rejects repeated images, stale inferences and insufficient quality. Accuracy takes priority over the earlier one-frame speed setting.
- A database revision invalidates the identity cache on enrollment, edits and removal, including changes from another connection.
- Each group attendance observation uses one database connection and one transaction.
- Group recognition supplies live facial landmarks, eliminating the separate preview detection requests while scanning.
- Attendance polling uses one overview request and skips identical table renders. Parent polling skips identical content, waits at most 15 seconds and avoids overlapping scheduled refreshes.
- Search and status filters make the roster easier to review. Camera connection can be cancelled; paused scans abort pending client requests; expired staff sessions return to sign-in.

Validation recorded for the 8 October update: 103 Python tests and three JavaScript camera lifecycle tests passed. An isolated browser test using prerecorded public sample faces confirmed two simultaneous check-ins, ten landmarks and a filtered present roster. User attendance and face records were not used for this test. A private SQLite backup was made before the database migration.

Recognition accuracy and liveness have not been independently validated. Photos can still match; this remains a local prototype.
