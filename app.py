#!/usr/bin/env python3
"""Agent Console - self-hosted ops console for Muse users.

Stdlib-only Python 3.12. Serves a small web UI plus a JSON API:
  file explorer (tree, breadcrumbs, ranked search, type filters,
  recents, preview), activity stream, and fleet overview.

No authentication: this build is meant for a trusted LAN. Anyone who
can reach the port can read the data.

Data is read from JSON files in DATA_DIR (default /data), produced by
export_data.py from whatever sync pipeline the operator runs. The app
is read-only: it never writes to DATA_DIR.

Environment:
  DATA_DIR  directory holding files.json, contents.json, fleet.json,
            activity.json            (default: /data)
  PORT      listen port              (default: 8080)
  HOST      listen address           (default: 0.0.0.0)
"""
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA_DIR = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "0.0.0.0")


# ---------------------------------------------------------------- data files

_data_cache: dict[str, tuple[float, object]] = {}


def load_data(name: str, default):
    """Load a JSON file from DATA_DIR, cached by mtime."""
    path = os.path.join(DATA_DIR, name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return default
    cached = _data_cache.get(name)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path) as f:
            data = json.load(f)
    except (ValueError, OSError):
        return default
    _data_cache[name] = (mtime, data)
    return data


def manifest_entries() -> list:
    data = load_data("files.json", [])
    if isinstance(data, dict):            # tolerate {"entries": [...]}
        data = data.get("entries", [])
    return data if isinstance(data, list) else []


def file_contents() -> dict:
    data = load_data("contents.json", {})
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- tree/search

def list_dir(entries: list, path: str):
    prefix = (path + "/") if path else ""
    dirs: dict[str, str] = {}
    files: dict[str, dict] = {}
    for e in entries:
        p = e.get("path", "")
        if not p.startswith(prefix):
            continue
        rest = p[len(prefix):]
        if not rest:
            continue
        seg = rest.split("/", 1)[0]
        full = prefix + seg
        if "/" in rest or e.get("kind") == "dir":
            dirs.setdefault(seg, full)
        else:
            files.setdefault(seg, e)
    dir_list = [{"name": n, "path": p} for n, p in sorted(dirs.items())]
    file_list = [{"name": n, "path": e.get("path", ""),
                  "size": e.get("size"), "modified_at": e.get("modified_at")}
                 for n, e in sorted(files.items())]
    return dir_list, file_list


def breadcrumbs(path: str) -> list:
    crumbs = [{"name": "~", "path": ""}]
    if path:
        acc = []
        for seg in path.split("/"):
            acc.append(seg)
            crumbs.append({"name": seg, "path": "/".join(acc)})
    return crumbs


def ranked_search(entries: list, query: str, ftype: str = "", limit: int = 50):
    q = query.strip().lower()
    if not q:
        return []
    scored = []
    for e in entries:
        if e.get("kind") == "dir":
            continue
        p = e.get("path", "")
        if ftype and not _type_match(p, ftype):
            continue
        name = p.rsplit("/", 1)[-1].lower()
        pl = p.lower()
        if name.startswith(q):
            score = (0, len(p))
        elif q in name:
            score = (1, name.index(q), len(p))
        elif pl.startswith(q):
            score = (2, len(p))
        elif q in pl:
            score = (3, pl.index(q), len(p))
        else:
            continue
        scored.append((score, {"path": p, "name": p.rsplit("/", 1)[-1],
                              "size": e.get("size"),
                              "modified_at": e.get("modified_at")}))
    scored.sort(key=lambda s: s[0])
    return [s[1] for s in scored[:limit]]


TYPE_GROUPS = {
    "code": (".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".go", ".rs", ".java",
             ".c", ".h", ".cpp", ".rb", ".php", ".css", ".html", ".json",
             ".yml", ".yaml", ".toml", ".md", ".sql", ".dockerfile"),
    "docs": (".md", ".txt", ".rst", ".pdf", ".docx"),
    "data": (".json", ".csv", ".tsv", ".xml", ".yaml", ".yml", ".db",
             ".sqlite"),
    "media": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp3", ".wav",
              ".mp4", ".webm"),
}


def _type_match(path: str, ftype: str) -> bool:
    exts = TYPE_GROUPS.get(ftype)
    if not exts:
        return True
    return path.lower().endswith(exts)

# ---------------------------------------------------------------- HTTP layer

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json",
        ".png": "image/png", ".ico": "image/x-icon", ".svg": "image/svg+xml"}


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentConsole/1.0"

    # -- helpers ----------------------------------------------------
    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, rel):
        rel = rel.lstrip("/") or "index.html"
        if ".." in rel or rel.startswith("/"):
            return self._send_json({"error": "not found"}, 404)
        path = os.path.join(STATIC_DIR, rel)
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")
        if not os.path.isfile(path):
            return self._send_json({"error": "not found"}, 404)
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter logs
        pass

    # -- routing ----------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        if route == "/api/tree":
            path = qs.get("path", [""])[0].strip().strip("/")
            dirs, files = list_dir(manifest_entries(), path)
            return self._send_json({"path": path, "breadcrumbs": breadcrumbs(path),
                                    "dirs": dirs, "files": files})
        if route == "/api/search":
            q = qs.get("q", [""])[0]
            ftype = qs.get("type", [""])[0]
            return self._send_json({"query": q,
                                    "results": ranked_search(manifest_entries(), q, ftype)})
        if route == "/api/file":
            path = qs.get("path", [""])[0].strip().strip("/")
            entries = manifest_entries()
            meta = next((e for e in entries
                         if e.get("path") == path and e.get("kind") != "dir"), None)
            if meta is None:
                return self._send_json({"error": "not found"}, 404)
            entry = file_contents().get(path) or {}
            content = entry.get("content")
            return self._send_json({"path": path, "size": meta.get("size"),
                                    "modified_at": meta.get("modified_at"),
                                    "content": content,
                                    "preview_available": content is not None})
        if route == "/api/fleet":
            fleet = load_data("fleet.json", None)
            if not fleet:
                return self._send_json({"available": False})
            fleet = dict(fleet)
            fleet["available"] = True
            return self._send_json(fleet)
        if route == "/api/activity":
            events = load_data("activity.json", [])
            if not isinstance(events, list):
                events = []
            events = sorted(events, key=lambda e: str(e.get("ts", "")),
                            reverse=True)[:100]
            return self._send_json({"events": events})
        if route.startswith("/api/"):
            return self._send_json({"error": "not found"}, 404)
        if route == "/":
            return self._send_static("index.html")
        if route.startswith("/static/"):
            return self._send_static(route[len("/static/"):])
        return self._send_json({"error": "not found"}, 404)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"agent-console listening on {HOST}:{PORT} (data: {DATA_DIR})",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
