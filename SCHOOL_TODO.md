# School attendance workflow

- [x] Arrival/departure timestamps, campus status and movement history.
- [x] Automatic direction crossing with configurable axis and entry direction.
- [x] Switchable camera check-in/check-out and manual staff movement controls.
- [x] Staff campus counts, movement log and end-of-session reconciliation.
- [x] Student IDs, unique ID validation and class-specific roster snapshots.
- [x] Parent current status, calendar movements and export timestamps.
- [x] Regression, direction, authorization and browser verification.

This is a local school attendance workflow; production deployment, individual staff accounts and validated anti-spoofing remain outside this implementation.

## registry and unattended recovery

- [x] student admission number, full name, grade, section and academic year
- [x] staff employee code and full name
- [x] save school records first and select a record for camera or iphone face enrollment
- [x] include registered people in rosters with manual attendance before face enrollment
- [x] student and staff roster selection and academic year filtering
- [x] preserve school details in historical rosters and csv exports
- [x] authenticated reconnect, request deadlines, exponential backoff and one connection notice
- [x] restore the camera source and the same active attendance session after an outage
- [x] honor manual pause and session end during recovery and reject late scan writes
- [x] reduce empty-frame requests, idle uploads and redundant polling
- [x] isolate stalled https handshakes from other requests
- [x] restart unexpected server exits while preserving the original network address

keep the designated camera page open and the device unlocked. browsers and the operating system may suspend a background or locked camera. network recovery does not record attendance while the camera cannot reach the laptop.
