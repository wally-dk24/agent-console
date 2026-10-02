# Agent Console

A self-hosted ops console for Muse users: a web UI over your agent
workspace with a findable file explorer, an activity stream, and a fleet
overview. One small stdlib-only Python process, no build step, no
dependencies, no secrets baked in.

No authentication: this build is meant for a trusted LAN. Anyone who can
reach the port can read the data. Do not expose it to the internet.

## Run it

```bash
# 1. Prepare a data directory (the entrypoint chowns it to PUID:PGID at
#    startup, so a manual chown is optional).
mkdir -p /srv/agent-console/data

# 2. Feed it data (see "Feeding data" below) into /srv/agent-console/data/:
#      files.json, contents.json, fleet.json, activity.json

# 3. Run (this sandbox needs --net=host; port comes from $PORT):
podman run -d --name agent-console --net=host \
  -v /srv/agent-console/data:/data \
  -e PORT=8080 \
  -e PUID=1000 -e PGID=1000 \
  docker.io/wallydk24/agent-console:latest

# Docker instead of podman:
docker run -d --name agent-console \
  -v /srv/agent-console/data:/data \
  -p 8080:8080 -e PORT=8080 \
  -e PUID=1000 -e PGID=1000 \
  docker.io/wallydk24/agent-console:latest
```

The container starts as root just long enough to recursively chown the data
directory to `PUID:PGID` (defaults 1000:1000), then drops privileges before
the app starts — the app itself never runs as root. Set `PUID`/`PGID` to match
the owner of your data directory and skip the manual chown entirely.

Then open `http://<host>:8080` — it opens straight into the console.

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

All JSON, no auth.

- `GET /api/tree?path=` → breadcrumbs, dirs, files
- `GET /api/search?q=&type=` → ranked matches (types: code, docs, data, media)
- `GET /api/file?path=` → metadata + preview content
- `GET /api/fleet`, `GET /api/activity`

## Notes

- The app is read-only: it never writes to the data directory.
- Serve on a trusted network only; the app itself is plain HTTP with no
  login. Do not expose it to the internet.

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
