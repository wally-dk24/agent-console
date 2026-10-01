#!/usr/bin/env python3
"""Export Agent Console data files from the ops-console sync pipeline.

Reads the pipeline outputs (never the artifact) and writes the plain JSON
files the agent-console container serves from its data volume:

  files.json     [{"path","kind","size","modified_at"}, ...]  (file tree)
  contents.json  {"path": {"content": str, "mtime": int}, ...} (previews)
  fleet.json     {captured_at, quota_used, quota_limit, active_workers,
                  pending_approvals, cron_healthy, cron_total, note}
                  (copied from --fleet-json, or {"available": false})
  activity.json  [{event_key, category, outcome, worker, action,
                  summary, ts}, ...]  (copied from --activity-json, or [])

The pipeline's contents.json stores file bodies base64-encoded; they are
decoded to text here ( undecodable entries are skipped ).

Usage:
  python3 export_data.py [--data-dir DIR]
                         [--fleet-json PATH] [--activity-json PATH]
"""
import argparse
import base64
import json
import os
import sys

OPS = os.path.expanduser("~/workspace/ops-console")


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "/data"))
    ap.add_argument("--fleet-json", default=None)
    ap.add_argument("--activity-json", default=None)
    args = ap.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)

    # file tree
    manifest = load_json(os.path.join(OPS, "out", "manifest.json"), {})
    entries = manifest.get("entries", []) if isinstance(manifest, dict) else []
    with open(os.path.join(args.data_dir, "files.json"), "w") as f:
        json.dump(entries, f)
    print(f"files.json: {len(entries)} entries")

    # file contents (base64 -> text)
    contents = load_json(os.path.join(OPS, "out", "contents.json"), {})
    items = contents.get("files", []) if isinstance(contents, dict) else []
    out = {}
    skipped = 0
    for item in items:
        path = item.get("path")
        raw = item.get("content", "")
        if not path:
            continue
        try:
            text = base64.b64decode(raw).decode("utf-8", errors="strict")
        except Exception:
            skipped += 1
            continue
        out[path] = {"content": text, "mtime": item.get("mtime")}
    with open(os.path.join(args.data_dir, "contents.json"), "w") as f:
        json.dump(out, f)
    print(f"contents.json: {len(out)} files ({skipped} undecodable skipped)")

    # fleet snapshot (operator-supplied; the pipeline pushes these to the
    # hosted artifact each run, so point this at a saved snapshot or skip)
    fleet = load_json(args.fleet_json, None) if args.fleet_json else None
    with open(os.path.join(args.data_dir, "fleet.json"), "w") as f:
        json.dump(fleet if fleet else {"available": False}, f)
    print(f"fleet.json: {'snapshot' if fleet else 'no snapshot (empty)'}")

    # activity events (operator-supplied anomaly events)
    activity = load_json(args.activity_json, []) if args.activity_json else []
    if not isinstance(activity, list):
        activity = []
    with open(os.path.join(args.data_dir, "activity.json"), "w") as f:
        json.dump(activity, f)
    print(f"activity.json: {len(activity)} events")


if __name__ == "__main__":
    sys.exit(main())
