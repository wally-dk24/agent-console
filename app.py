#!/usr/bin/env python3
"""Agent Console - thin API client for the Pi.

Serves the web UI (static/) and proxies /api/* to the Agent Console
Data API (Cloudflare Worker) using a bearer token. The browser never
sees the token: the frontend calls this server on localhost, and this
server attaches `Authorization: Bearer <API_TOKEN>` upstream.

No local data files. No volumes. All data comes from the API.

Environment:
  API_BASE_URL  e.g. https://agent-console-api.<sub>.workers.dev (required)
  API_TOKEN     bearer token for the data API                  (required)
  PORT          listen port                                     (default: 8080)
  HOST          listen address                                  (default: 0.0.0.0)
"""
import base64
import json
import os
import ssl
import tempfile
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _install_extra_ca():
    """Trust the bundled egress-proxy CA alongside system roots.

    In sandboxed build/test environments outbound HTTPS is TLS-intercepted;
    the interceptor's CA is bundled at /app/hatch-egress-ca.crt. On a
    normal network (e.g. Master's Pi) the file is absent and this is a
    no-op — system roots are used as-is.
    """
    ca_path = "/app/hatch-egress-ca.crt"
    if not os.path.exists(ca_path):
        return
    try:
        system_ca = ssl.get_default_verify_paths().cafile
        bundle = open(system_ca, "rb").read() if system_ca and os.path.exists(system_ca) else b""
        bundle += b"\n" + open(ca_path, "rb").read()
        fd, tmp = tempfile.mkstemp(prefix="ca-bundle-", suffix=".pem")
        os.write(fd, bundle)
        os.close(fd)
        os.environ["SSL_CERT_FILE"] = tmp
    except Exception:
        pass


_install_extra_ca()

API_BASE_URL = os.environ.get("API_BASE_URL", "").rstrip("/")
API_TOKEN = os.environ.get("API_TOKEN", "")
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "0.0.0.0")

if not API_BASE_URL or not API_TOKEN:
    raise SystemExit("API_BASE_URL and API_TOKEN env vars are required.")

# ---------------------------------------------------------------- upstream

def api_get(path, params=None):
    """GET the data API, returning (status, dict)."""
    url = API_BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + API_TOKEN,
                      "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/120.0 Safari/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"ok": False, "error": "upstream error"}
    except Exception as e:
        return 503, {"ok": False, "error": f"api unreachable: {e}"}

# ---------------------------------------------------------------- shapes

def breadcrumbs(path: str) -> list:
    crumbs = [{"name": "~", "path": ""}]
    if path:
        acc = []
        for seg in path.split("/"):
            acc.append(seg)
            crumbs.append({"name": seg, "path": "/".join(acc)})
    return crumbs


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


def ranked(results: list, query: str):
    """Rank search hits: name-prefix > name-substring > path-prefix > path-substring."""
    q = query.strip().lower()
    scored = []
    for e in results:
        p = e.get("path", "")
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
        scored.append((score, e))
    scored.sort(key=lambda s: s[0])
    return [s[1] for s in scored]


def decode_content(b64: str) -> str:
    try:
        return base64.b64decode(b64).decode("utf-8", errors="replace")
    except Exception:
        return "(undecodable content)"


# ---------------------------------------------------------------- HTTP layer

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json",
        ".png": "image/png", ".ico": "image/x-icon", ".svg": "image/svg+xml"}


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentConsole/2.0"

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

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route, qs = parsed.path, urllib.parse.parse_qs(parsed.query)

        if route == "/api/tree":
            path = qs.get("path", [""])[0].strip().strip("/")
            status, data = api_get("/api/v1/files", {"path": path or "/"})
            if status != 200 or not data.get("ok"):
                return self._send_json(
                    {"error": data.get("error", "api error")}, status)
            dirs, files = [], []
            for e in data.get("entries", []):
                name = e["path"].rsplit("/", 1)[-1]
                if e.get("kind") == "dir":
                    dirs.append({"name": name, "path": e["path"]})
                else:
                    files.append({"name": name, "path": e["path"],
                                  "size": e.get("size"),
                                  "modified_at": e.get("mtime")})
            return self._send_json({"path": path,
                                    "breadcrumbs": breadcrumbs(path),
                                    "dirs": dirs, "files": files})

        if route == "/api/search":
            q = qs.get("q", [""])[0]
            ftype = qs.get("type", [""])[0]
            status, data = api_get("/api/v1/search", {"q": q, "limit": "50"})
            if status != 200 or not data.get("ok"):
                return self._send_json(
                    {"query": q, "results": [],
                     "error": data.get("error", "api error")}, status)
            results = [e for e in data.get("results", [])
                       if _type_match(e.get("path", ""), ftype)]
            results = ranked(results, q)[:50]
            out = [{"path": e["path"], "name": e["path"].rsplit("/", 1)[-1],
                    "size": e.get("size"), "modified_at": e.get("mtime")}
                   for e in results]
            return self._send_json({"query": q, "results": out})

        if route == "/api/file":
            path = qs.get("path", [""])[0].strip().strip("/")
            status, data = api_get("/api/v1/file", {"path": path})
            if status == 404:
                return self._send_json({"error": "not found"}, 404)
            if status != 200 or not data.get("ok"):
                return self._send_json(
                    {"error": data.get("error", "api error")}, status)
            b64 = data.get("content") or ""
            return self._send_json({"path": path,
                                    "size": len(b64) * 3 // 4,
                                    "modified_at": data.get("mtime"),
                                    "content": decode_content(b64),
                                    "preview_available": bool(b64)})

        if route == "/api/fleet":
            status, data = api_get("/api/v1/fleet")
            if status != 200 or not data.get("ok"):
                return self._send_json({"available": False})
            fleet = dict(data.get("fleet") or {})
            fleet["available"] = True
            return self._send_json(fleet)

        if route == "/api/activity":
            status, data = api_get("/api/v1/activities", {"limit": "100"})
            if status != 200 or not data.get("ok"):
                return self._send_json({"events": []}, status)
            return self._send_json({"events": data.get("events", [])})

        if route == "/api/health":
            status, data = api_get("/api/v1/health")
            return self._send_json({"proxy": "ok", "api": data}, status)

        if route.startswith("/api/"):
            return self._send_json({"error": "not found"}, 404)
        if route == "/":
            return self._send_static("index.html")
        if route.startswith("/static/"):
            return self._send_static(route[len("/static/"):])
        return self._send_json({"error": "not found"}, 404)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"agent-console listening on {HOST}:{PORT} (api: {API_BASE_URL})",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
