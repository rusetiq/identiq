## 29 October 2026: per-person queues and repeated-input inference

Each provisional identity now has a four-frame queue. The queues share scene
images and upload each scene once; the server re-identifies every face instead
of trusting the queue's identity. Missing or ambiguous identities drop their
queues. The existing age, ordering and confirmation rules remain in force.

A bounded memory cache holds up to 80 SHA-256 hashes of exact aligned model
inputs and their validated embeddings for 1.5 seconds. It retains no camera
images or names. Changed inputs still run inference; quality, identity matching
and attendance checks always run. Returned arrays cannot mutate cached values.
Expired entries are removed on the next lookup, or when the process exits.

A local synthetic four-face CPU test measured 70.37 ms without reuse and 0.45 ms
with identical-input reuse (median of 15 warm runs, identical embeddings).
Detection was stubbed; this excludes detection, upload and matching. Core ML
was unavailable in the sandbox during this test. This is a duplicate-input
saving, not evidence of faster inference on new walking-face images. The normal
inference benchmark disables this cache to avoid inflated speedup reports.

# Performance and reliability update

## Walking confirmation buffer

Attendance scanning samples the camera approximately every 300 ms while an
inference is running. After a possible match, up to four recent frames are
processed in capture order. Frames older than 1.5 seconds are dropped before
submission, and the server rejects a frame if its reported buffer age is
invalid, it arrives out of capture order, or its reported age plus server work
exceeds three seconds. Requests remain sequential. Each frame independently
passes the existing quality, match, ambiguity, continuity and movement checks;
three distinct decoded images over at least 0.6 seconds are still required.

Capture age is reported by the camera device and is not proof that an image was
live. Duplicate detection hashes decoded pixels, so changing JPEG metadata
alone does not create another confirmation. This took a median 0.84 ms for a
synthetic 1280×720 frame over 30 runs on the development laptop. Delayed
offline frames are discarded rather than submitted as fresh attendance; brief
offline images in browser storage expire at the next queue cleanup.

## 29 October 2026: Apple silicon inference

On Apple silicon Macs with the Core ML execution provider, ArcFace now uses the
same model with hardware acceleration. At startup the app compares a synthetic
embedding with the CPU result and uses CPU inference if initialization or that
check fails. If accelerated inference fails or produces an invalid embedding,
the app retries on the CPU. Windows and other systems keep the existing CPU
path. Set `IDENTIQ_INFERENCE_BACKEND=cpu` before starting the server to use the
CPU explicitly.

Five alternating warm runs on the development Mac used the installed model and
synthetic 128-pixel crops. The time includes crop quality and alignment but
excludes face detection, network, database matching and the required attendance
confirmation period.

| Faces | CPU | Core ML | Speedup |
| --- | --- | --- | --- |
| 1 | 35.9 ms | 10.1 ms | 3.55× |
| 4 | 71.2 ms | 22.6 ms | 3.15× |
| 8 | 131.1 ms | 44.3 ms | 2.96× |
| 20 | 334.4 ms | 118.3 ms | 2.83× |

The largest normalized embedding difference across those samples was below
0.000001. This synthetic check does not establish real-world recognition
accuracy; evaluate genuine and impostor captures on each deployment. Matching
thresholds, quality limits and three-frame confirmation remain unchanged.
Initial compilation can take longer and stores about 333 MB of compiled model
files under `data/coreml-cache`; the cache contains model weights, not camera
frames or enrolled identity data.

## 28 October 2026

Group recognition now runs up to four independent ArcFace inferences concurrently
through the existing CPU session, capped by the machine's CPU count. Each inference
still uses one internal thread. Quality rejection runs first, detection order is
preserved when jobs finish out of order, and the engine finishes all jobs for one
frame before releasing its lock. Single-face inference uses the direct path.

Five alternating warm runs on the development laptop measured these medians using
the installed ArcFace model and synthetic crops:

| Faces | Serial | Parallel | Speedup |
| --- | --- | --- | --- |
| 1 | 35.18 ms | 35.22 ms | 1.00× |
| 4 | 140.99 ms | 68.50 ms | 2.06× |
| 8 | 279.68 ms | 123.37 ms | 2.27× |
| 20 | 703.42 ms | 303.79 ms | 2.32× |

Normalized embeddings were identical in these runs. This measures quality checks,
alignment and inference, excluding detection, matching, camera capture and Wi-Fi.
School hardware and real-world recognition performance still require measurement.
Reproduce with `.venv/bin/python scripts/benchmark_inference.py --runs 5`; it never
opens the identity database or camera.

Ordinary recognition waits 150 ms between visible-face requests instead of
650 ms. Attendance scheduling accounts for the previous inference duration when
estimating when to send the next frame, with a 40 ms minimum delay and slower
empty-scene polling. The later walking confirmation buffer described above
supersedes the previous capture-on-request behavior. Server-side frame freshness,
quality, similarity, ambiguity, spacing and three-frame confirmation checks remain
authoritative. Restart the server and refresh camera pages to load these changes.

## 22 October 2026

A disposable benchmark with 1,000 synthetic identities, 5,000 embeddings and 20 queries per frame measured a median **3.289 ms** for the full candidate ranking versus **0.417 ms** for exact top-two selection across 40 alternating runs on the development laptop (7.89× for this matching stage). The top two IDs and scores were identical. Candidate objects allocated per frame fell from 20,000 to 40. This excludes camera capture, Wi-Fi, face detection and embedding inference; it is not an end-to-end speed or school-PC accuracy measurement.

Reproduce with `.venv/bin/python scripts/benchmark_matching.py --identities 1000 --faces 20 --runs 40`. It creates and removes its own synthetic database and does not read school records.

- Every identity is still scored. Recognition retains the exact global winner and runner-up without sorting/materializing the entire directory; selected-person verification and enrollment retain their full candidate checks.
- Attendance applies the existing quality cutoffs before running ArcFace. Small, blurred or weakly detected faces retain their adjustment messages and landmarks, without spending embedding/matching work on rejected faces. The same cutoffs remain enforced at the attendance write boundary.
- The server supplies the next useful scan delay. The tablet waits for confirmation timing instead of submitting bursts that cannot count, and empty or unusable scenes use a slower cadence. Requests remain sequential, with a bounded client fallback.
- A group loads its matching roster records with one query instead of one per face. Already checked-in students refresh last-seen at most once every five seconds; actual check-in/out timestamps and new movements are written immediately.
- Connection check now includes the last camera request round trip, server scan processing time, face count and sample time. The ordinary reachability check remains separate. Diagnostic samples retain only numbers and a timestamp, with no identity, image or face pattern.

The 0.65 similarity threshold, 0.12 runner-up margin, three-frame confirmation, minimum confirmation duration, continuity, stale-frame and manual-correction safeguards remain in force. The 0.65 cutoff is temporary and uncalibrated; real tablet recognition accuracy still needs deployment-specific validation.

Validation for this update: 174 Python checks and 22 JavaScript checks passed, including tied candidate scores, selected-person claims, group query counts, early quality rejection, three-frame pacing, last-seen refreshes and diagnostic privacy. These use synthetic inputs and controlled inference, not a field accuracy benchmark.

## Weak-connection delivery update

The 13 first-party CSS/JavaScript assets measured 132,379 bytes before compression and 39,062 bytes with startup gzip compression, about 70.5% smaller in aggregate (sizes vary with later changes). Versioned public assets can be reused without redownloading, and HTML includes its own stylesheet with a matching CSP hash. HTML is gzip-compressed when accepted while remaining `no-store`; private APIs and records are not cached.

Shared preview traffic is reduced independently of recognition image quality: normal previews use 480 pixels/up to 2 FPS, constrained previews 360 pixels/up to 1 FPS, and socket backpressure suppresses additional preview captures. Connection diagnostics sample five bounded requests instead of relying on one successful response. This has not been measured on the school's second-floor Wi-Fi and cannot establish or repair network coverage/routing.

## Earlier baseline from 8 October 2026

Measured locally on 8 October 2026 with 1,000 synthetic identities, 5,000 normalized face embeddings and eight queries per frame. The previous code took 46.17 ms (median of six runs). Batched cached matching takes 1.55 ms (median of 20 warm runs); first-load time is 10.26 ms. This measures database loading and candidate scoring, excluding camera capture, detection and embedding inference. It is not an overall FPS or accuracy claim.

- Recognition scores still average enrolled sample similarities. Recognition now requires similarity 0.65 and a 0.12 runner-up margin. Automatic attendance requires three fresh, clear, spatially continuous observations over at least 0.6 seconds, with at least 0.25 seconds between contributing frames. A single fast result cannot mark attendance. Requests remain sequential and use the existing scan cadence; the server rejects repeated images, stale inferences and insufficient quality. Accuracy takes priority over the earlier one-frame speed setting.
- A database revision invalidates the identity cache on enrollment, edits and removal, including changes from another connection.
- Each group attendance observation uses one database connection and one transaction.
- Group recognition supplies live facial landmarks, eliminating the separate preview detection requests while scanning.
- Attendance polling uses one overview request and skips identical table renders. Parent polling skips identical content, waits at most 15 seconds and avoids overlapping scheduled refreshes.
- Search and status filters make the roster easier to review. Camera connection can be cancelled; paused scans abort pending client requests; expired staff sessions return to sign-in.

Validation recorded for the 8 October update: 103 Python tests and three JavaScript camera lifecycle tests passed. An isolated browser test using prerecorded public sample faces confirmed two simultaneous check-ins, ten landmarks and a filtered present roster. User attendance and face records were not used for this test. A private SQLite backup was made before the database migration.

Recognition accuracy and liveness have not been independently validated. Photos can still match; this remains a local prototype.
