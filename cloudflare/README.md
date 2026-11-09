# Cloud attendance API

This Worker stores school profiles, face-enrollment status and attendance snapshots in D1. ArcFace enrollment and scanning continue on the school server. Face photos, embeddings, passwords and parent credentials are not uploaded. This is a cloud integration service, not a replacement for the local recognition server.

## Admin setup

Deploy using `npx --yes wrangler@4 deploy --config cloudflare/wrangler.jsonc`, then run `python scripts/configure_cloud.py https://YOUR-WORKER.workers.dev`. The configuration script installs the Worker `ADMIN_TOKEN` secret and saves it in the ignored `data/cloud-api.json` with owner-only permissions. Only the authenticated local staff page can use the key-management proxy; the master credential is never returned to the browser.

Restart the app, sign in as staff, and open **Cloud API**. Create a **Connect this school server** key with server ID `school-main`. This installs the scanner key locally and starts initial sync. Then create a **Read** or **Read and request manual updates** key using the same server ID for the separate school service. Copy each key once; stored Worker keys are SHA-256 hashes. Revoke keys from the same panel. A scanner key can upload snapshots and process commands; read/write integration keys cannot impersonate scans. Different server IDs isolate different schools' records.

All requests use `Authorization: Bearer iq_...` over HTTPS. Keep the key in the other service's backend, never in its public frontend. No cross-origin browser access is enabled. This app currently has a single shared staff/admin role, so every signed-in staff user can manage these keys.

## Routes

| Method | Route | Access | Result |
| --- | --- | --- | --- |
| GET | `/health` | public | Service health only |
| GET | `/v1/people?limit=50&offset=0` | read/write/scanner | Profiles and enrollment sample counts |
| POST | `/v1/people` | write | Queue a new school profile |
| GET | `/v1/attendance/sessions?limit=50&offset=0` | read/write/scanner | Synced sessions with rosters |
| GET | `/v1/attendance/sessions/SESSION_ID` | read/write/scanner | Synced session and last sync time |
| PATCH | `/v1/attendance/sessions/SESSION_ID/records/RECORD_ID` | write | Queue status correction |
| POST | `/v1/attendance/sessions/SESSION_ID/records/RECORD_ID/movement` | write | Queue manual check-in/out |
| GET | `/v1/commands/COMMAND_ID` | read/write/scanner | `pending`, `applied` or `rejected`, with validation result |
| POST | `/v1/sync` | scanner | Idempotent local snapshot upload |
| GET | `/v1/commands` | scanner | Pending validation commands |
| POST | `/v1/commands/COMMAND_ID` | scanner | Acknowledge local validation |
| GET/POST | `/admin/keys` | admin master | List/create scoped keys |
| DELETE | `/admin/keys/KEY_ID` | admin master | Revoke key |

Write requests require an `Idempotency-Key` header unique to the intended action. Retry the same action with the same header to avoid duplicate commands. Reusing it with different content returns 409. A 202 response means queued, not saved attendance: poll the returned command ID. The local school server checks active sessions, roster membership, status and movement rules before acknowledging. Pending commands wait while the school server is offline. Synced reads return their `synced_at` timestamp so consumers can identify stale records.

Create a student with `{"name":"Example Student","person_type":"student","admission_number":"S100","grade":"7","section":"A","academic_year":"2026"}`. Staff profiles require `name`, `person_type:"staff"`, and `employee_code`. New profiles appear in the local enrollment selector after sync; capture five face samples there. Enrollment metadata is then synced back to the cloud. The Worker does not accept raw face enrollment images.

Correct a status with `{"status":"present"}`, `absent`, or `pending`. Record movement with `{"action":"check_in","note":"verified by staff"}` or `check_out`. Use the roster's record `id`, not the profile ID. Cloud manual movements are marked as staff actions and include their command ID in the local audit note.

The local server retries outbox uploads in the background every ten seconds. Scans do not wait on internet requests. Successful scans and local enrollment/profile changes enqueue snapshots. Cloud availability does not repair tablet-to-school-server Wi-Fi problems or permit offline recognition. Source IDs and credentials must be consistent when migrating a school server. Database backups and deletion policies should include the Worker D1 database as well as the laptop.
