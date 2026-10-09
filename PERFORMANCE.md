# Performance and reliability update

Measured locally on 8 October 2026 with 1,000 synthetic identities, 5,000 normalized face embeddings and eight queries per frame. The previous code took 46.17 ms (median of six runs). Batched cached matching takes 1.55 ms (median of 20 warm runs); first-load time is 10.26 ms. This measures database loading and candidate scoring, excluding camera capture, detection and embedding inference. It is not an overall FPS or accuracy claim.

- Recognition scores still average enrolled sample similarities; thresholds are unchanged. Attendance now uses one successful match; standalone recognition still uses three. Attendance scans wait 40 ms after each response instead of 650 ms.
- A database revision invalidates the identity cache on enrollment, edits and removal, including changes from another connection.
- Each group attendance observation uses one database connection and one transaction.
- Group recognition supplies live facial landmarks, eliminating the separate preview detection requests while scanning.
- Attendance polling uses one overview request and skips identical table renders. Parent polling skips identical content, waits at most 15 seconds and avoids overlapping scheduled refreshes.
- Search and status filters make the roster easier to review. Camera connection can be cancelled; paused scans abort pending client requests; expired staff sessions return to sign-in.

Validation: 103 Python tests and three JavaScript camera lifecycle tests pass. An isolated browser test using prerecorded public sample faces confirmed two simultaneous check-ins, ten landmarks and a filtered present roster. User attendance and face records were not used for this test. A private SQLite backup was made before the database migration.

Recognition accuracy and liveness have not been independently validated. Photos can still match; this remains a local prototype.
