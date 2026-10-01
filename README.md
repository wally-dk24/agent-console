# Agent Console

A self-hosted ops console for Muse users: a private web UI over your agent
workspace with password + TOTP auth, a findable file explorer, an activity
stream, and a fleet overview. One small stdlib-only Python process, no build
step, no dependencies, no secrets baked in.

## Run it

```bash
# 1. Prepare a data directory the container can write (it stores .auth.json).
mkdir -p /srv/agent-console/data
chown 1000:1000 /srv/agent-console/data        # image runs as USER 1000

# 2. Feed it data (see "Feeding data" below) into /srv/agent-console/data/:
#      files.json, contents.json, fleet.json, activity.json

# 3. Run (this sandbox needs --net=host; port comes from $PORT):
podman run -d --name agent-console --net=host \
  -v /srv/agent-console/data:/data \
  -e PORT=8080 \
  docker.io/wallydk24/agent-console:latest

# Docker instead of podman:
docker run -d --name agent-console \
  -v /srv/agent-console/data:/data \
  -p 8080:8080 -e PORT=8080 \
  docker.io/wallydk24/agent-console:latest
```

Then open `http://<host>:8080`.

## First-run enrollment

1. Open the console. You'll see **Private by default.** — choose a password
   (minimum 12 characters) and click **Begin secure setup**.
2. **Secure this console.** shows an authenticator setup key (copy button —
   shown once). Add it to your authenticator app via manual key entry.
3. Type the app's 6-digit code. **Finish setup** enables once the code is
   entered. You're in.

Later visits ask for password + the current 6-digit code. Sessions last 24h.

## Feeding data

The console reads plain JSON from its data volume (`$DATA_DIR`, default
`/data`). Re-run your exporter whenever you want fresher data; the app
picks up changed files automatically.

`export_data.py` (in this repo) converts the ops-console sync pipeline
output:

```bash
python3 export_data.py --data-dir /srv/agent-console/data \
  --fleet-json /path/to/fleet-snapshot.json \
  --activity-json /path/to/activity-events.json
```

Formats:

- `files.json`: `[{"path": "a/b.txt", "kind": "file", "size": 123,
  "modified_at": "2026-10-01T20:41:48Z"}, {"path": "a", "kind": "dir"}, ...]`
- `contents.json`: `{"a/b.txt": {"content": "full text…", "mtime": 1790887308}}`
  (previews; files missing here show "preview not synced")
- `fleet.json`: `{"captured_at": "…Z", "quota_used": 11, "quota_limit": 100,
  "active_workers": 0, "pending_approvals": 0, "cron_healthy": 48,
  "cron_total": 48, "note": "…"}`
- `activity.json`: `[{"event_key": "…", "category": "status",
  "outcome": "attention", "worker": "…", "action": "…",
  "summary": "…", "ts": "…Z"}]`

Any Muse user can point this at their own workspace: write these four
files from your own file list and stats — nothing in the app is
Wally-specific.

## API

All JSON. Session cookie `ac_session` (HttpOnly, 24h).

- `GET /api/auth-state` → `{"configured": true|false}`
- `POST /api/enroll` `{"password"}` → `{"setup_key"}` (first run only)
- `POST /api/enroll/verify` `{"code"}` → sets session
- `POST /api/login` `{"password","code"}` → sets session
- `POST /api/logout`
- `GET /api/tree?path=` → breadcrumbs, dirs, files
- `GET /api/search?q=&type=` → ranked matches (types: code, docs, data, media)
- `GET /api/file?path=` → metadata + preview content
- `GET /api/fleet`, `GET /api/activity`

## Security notes

- Passwords are PBKDF2-HMAC-SHA256 (200k iterations); TOTP is RFC 6238
  (SHA-1, 30s, 6 digits, ±1 step); sessions are `secrets` tokens.
- Auth material lives only in `$DATA_DIR/.auth.json` (mode 600), created at
  enrollment. Back it up — losing it means re-enrolling (data files are
  unaffected).
- Login is rate-limited (10 attempts/minute per IP).
- Serve behind HTTPS / on a trusted network; the app itself is plain HTTP.

## Build

Multi-arch (`linux/amd64`, `linux/arm64`, `linux/386`), RUN-free Dockerfile:

```bash
podman --storage-driver vfs --cgroup-manager=cgroupfs build --network=host \
  --platform linux/amd64 -t localhost/agent-console:amd64 .
# repeat for linux/arm64 and linux/386, then:
podman manifest create agent-console:latest
podman manifest add agent-console:latest containers-storage:localhost/agent-console:amd64
# ... arm64, 386 ...
podman manifest push --all agent-console:latest \
  docker://docker.io/wallydk24/agent-console:latest
```
