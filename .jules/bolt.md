## 2026-10-22 - Avoid `frame.tobytes()` Buffer Allocations for Frame Hashing
**Learning:** Calculating `frame_id` hashes using `frame.tobytes()` on uncompressed NumPy BGR arrays (640x480 or 1280x960) allocates 1–3.7MB per frame and takes ~12ms to hash per group frame. Hashing the incoming base64 payload string directly is ~65x faster (~0.18ms) and avoids buffer allocations.
**Action:** When deduplicating incoming API camera payloads, hash the base64 or raw JPEG payload string directly instead of converting decoded image arrays back to raw bytes via `tobytes()`.

## 2026-10-22 - Convert to Grayscale Before Resizing Image Crops & Fail Fast on Face Dimensions
**Learning:** Resizing a 3-channel BGR face crop before `cv2.cvtColor` to grayscale performs 3x as many pixel interpolations as converting to 1-channel grayscale first. Additionally, computing sharpness (Laplacian variance) on faces smaller than the minimum size threshold wastes CPU cycles on disused crops. Reordering size checks and converting to grayscale before `cv2.resize` speeds up quality analysis by ~73%.
**Action:** Always filter candidate bounding boxes before image transformations, and convert BGR crops to grayscale prior to spatial resizing when only single-channel feature metrics (like sharpness/brightness) are required.
