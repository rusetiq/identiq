## 2026-10-22 - Avoid `frame.tobytes()` Buffer Allocations for Frame Hashing
**Learning:** Calculating `frame_id` hashes using `frame.tobytes()` on uncompressed NumPy BGR arrays (640x480 or 1280x960) allocates 1–3.7MB per frame and takes ~12ms to hash per group frame. Hashing the incoming base64 payload string directly is ~65x faster (~0.18ms) and avoids buffer allocations.
**Action:** When deduplicating incoming API camera payloads, hash the base64 or raw JPEG payload string directly instead of converting decoded image arrays back to raw bytes via `tobytes()`.

## 2026-10-27 - Reject Undersized Faces Before Quality Processing
**Learning:** Faces below the existing size limits can skip crop extraction, resizing, grayscale conversion and Laplacian variance. Swapping resize and grayscale conversion changes uint8 rounding and can flip decisions at the sharpness cutoffs, even though the unrounded operations are linear.
**Action:** Check face dimensions before image transformations and preserve the resize-then-grayscale order for qualifying faces. Keep regression coverage at both the recognition and attendance sharpness cutoffs.

## 2026-10-27 - Use Indexed Single-Record Query for Profile Reads
**Learning:** Returning updated records via `next(p for p in self.people() if p['id']==person_id)` executed a full database query joining all people and all embedding vector blobs, building $N$ Python dictionaries to find 1 record (~34.2 ms with 1,000 enrolled students). Querying `WHERE p.id=?` directly reduces latency by ~97% (~1.0 ms).
**Action:** Always fetch specific identity records using `WHERE p.id=?` instead of calling directory-wide `people()` queries and filtering in Python memory.

## 2026-10-30 - Reference Indexed Foreign Key Column in `ORDER BY` to Avoid Filesort
**Learning:** Even when joining on equality (`s.id = r.session_id`), specifying `ORDER BY s.id DESC` prevented SQLite from using the existing composite index `attendance_records(person_id, session_id DESC)` for ordering, causing SQLite to allocate a temporary B-tree (`USE TEMP B-TREE FOR ORDER BY`). Changing to `ORDER BY r.session_id DESC` allows SQLite to satisfy both the `WHERE r.person_id=?` filter and the `ORDER BY` directly from the index, eliminating the temp B-tree filesort and reducing lookup latency by ~35-50% (~1.5x speedup).
**Action:** In SQLite JOIN queries, write `ORDER BY` clauses using the indexed table's column (e.g. `r.session_id`) rather than the joined table's primary key (`s.id`) when an index with matching sort order exists on the former.
