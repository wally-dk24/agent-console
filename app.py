#!/usr/bin/env python3
"""Agent Console - self-hosted ops console for Muse users.

Stdlib-only Python 3.12. Serves a small web UI plus a JSON API:
  first-run password+TOTP enrollment, login/logout, file explorer
  (tree, breadcrumbs, ranked search, type filters, recents, preview),
  activity stream, and fleet overview.

Data is read from JSON files in DATA_DIR (default /data), produced by
export_data.py from whatever sync pipeline the operator runs. The image
is credential-free: the password hash and TOTP secret live in
DATA_DIR/.auth.json (mode 600), created at first-run enrollment.

Environment:
  DATA_DIR  directory holding files.json, contents.json, fleet.json,
            activity.json            (default: /data)
  PORT      listen port              (default: 8080)
  HOST      listen address           (default: 0.0.0.0)
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA_DIR = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "0.0.0.0")
AUTH_PATH = os.path.join(DATA_DIR, ".auth.json")

PBKDF2_ITERATIONS = 200_000
SESSION_TTL = 24 * 3600
COOKIE_NAME = "ac_session"

# ---------------------------------------------------------------- crypto

def hash_password(password: str) -> dict:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return {"salt": salt.hex(), "hash": dk.hex(), "iterations": PBKDF2_ITERATIONS}


def check_password(password: str, rec: dict) -> bool:
    try:
        salt = bytes.fromhex(rec["salt"])
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt,
                                 int(rec.get("iterations", PBKDF2_ITERATIONS)))
        return hmac.compare_digest(dk.hex(), rec["hash"])
    except Exception:
        return False


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()


def totp_code(secret_b32: str, at: int | None = None, step: int = 30,
              digits: int = 6) -> str:
    """RFC 6238 TOTP, SHA-1."""
    if at is None:
        at = int(time.time())
    key = base64.b32decode(secret_b32)
    counter = (at // step).to_bytes(8, "big")
    mac = hmac.new(key, counter, hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = int.from_bytes(mac[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def totp_verify(secret_b32: str, code: str, window: int = 1) -> bool:
    code = str(code).strip()
    if not code.isdigit():
        return False
    now = int(time.time())
    for w in range(-window, window + 1):
        if hmac.compare_digest(totp_code(secret_b32, now + w * 30), code):
            return True
    return False


# ---------------------------------------------------------------- auth state

_sessions: dict[str, float] = {}          # token -> created_at
_pending_enroll: dict | None = None       # {password: rec, secret: b32, at: ts}
_login_attempts: dict[str, list[float]] = {}  # ip -> [ts]


def load_auth() -> dict | None:
    try:
        with open(AUTH_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None


def save_auth(rec: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = AUTH_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTH_PATH)


def new_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time()
    return token


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    created = _sessions.get(token)
    if created is None:
        return False
    if time.time() - created > SESSION_TTL:
        _sessions.pop(token, None)
        return False
    return True


def rate_limited(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _login_attempts.get(ip, []) if now - t < 60]
    _login_attempts[ip] = hits
    if len(hits) >= 10:
        return True
    hits.append(now)
    return False


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
    def _send_json(self, obj, status=200, set_cookie=None, clear_cookie=False):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie",
                             f"{COOKIE_NAME}={set_cookie}; HttpOnly; "
                             f"SameSite=Lax; Path=/; Max-Age={SESSION_TTL}")
        if clear_cookie:
            self.send_header("Set-Cookie",
                             f"{COOKIE_NAME}=; HttpOnly; SameSite=Lax; "
                             f"Path=/; Max-Age=0")
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

    def _read_json_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if n <= 0 or n > 64 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except (ValueError, UnicodeDecodeError):
            return {}

    def _cookie_token(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith(COOKIE_NAME + "="):
                return part[len(COOKIE_NAME) + 1:]
        return None

    def _authed(self):
        return valid_session(self._cookie_token())

    def _require_auth(self):
        if not self._authed():
            self._send_json({"error": "unauthorized"}, 401)
            return False
        return True

    def log_message(self, fmt, *args):  # quieter logs
        pass

    # -- routing ----------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        if route == "/api/auth-state":
            auth = load_auth()
            return self._send_json({"configured": bool(auth and auth.get("verified"))})
        if route == "/api/tree":
            if not self._require_auth():
                return
            path = qs.get("path", [""])[0].strip().strip("/")
            dirs, files = list_dir(manifest_entries(), path)
            return self._send_json({"path": path, "breadcrumbs": breadcrumbs(path),
                                    "dirs": dirs, "files": files})
        if route == "/api/search":
            if not self._require_auth():
                return
            q = qs.get("q", [""])[0]
            ftype = qs.get("type", [""])[0]
            return self._send_json({"query": q,
                                    "results": ranked_search(manifest_entries(), q, ftype)})
        if route == "/api/file":
            if not self._require_auth():
                return
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
            if not self._require_auth():
                return
            fleet = load_data("fleet.json", None)
            if not fleet:
                return self._send_json({"available": False})
            fleet = dict(fleet)
            fleet["available"] = True
            return self._send_json(fleet)
        if route == "/api/activity":
            if not self._require_auth():
                return
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

    def do_POST(self):
        global _pending_enroll
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        ip = self.client_address[0]

        if route == "/api/enroll":
            auth = load_auth()
            if auth and auth.get("verified"):
                return self._send_json({"error": "already configured"}, 409)
            body = self._read_json_body()
            password = str(body.get("password", ""))
            if len(password) < 12:
                return self._send_json(
                    {"error": "Use at least 12 characters."}, 400)
            _pending_enroll = {"password": hash_password(password),
                               "secret": new_totp_secret(),
                               "at": time.time()}
            # Shown once, like the approved UX: the operator copies it now.
            return self._send_json({"setup_key": _pending_enroll["secret"]})

        if route == "/api/enroll/verify":
            if not _pending_enroll or time.time() - _pending_enroll["at"] > 600:
                _pending_enroll = None
                return self._send_json({"error": "enrollment expired"}, 410)
            body = self._read_json_body()
            code = str(body.get("code", ""))
            if not totp_verify(_pending_enroll["secret"], code):
                return self._send_json({"error": "wrong code"}, 401)
            save_auth({"password": _pending_enroll["password"],
                       "totp_secret": _pending_enroll["secret"],
                       "verified": True, "created_at": int(time.time())})
            _pending_enroll = None
            token = new_session()
            return self._send_json({"ok": True}, set_cookie=token)

        if route == "/api/login":
            if rate_limited(ip):
                return self._send_json(
                    {"error": "too many attempts, wait a minute"}, 429)
            auth = load_auth()
            body = self._read_json_body()
            ok = (auth and auth.get("verified")
                  and check_password(str(body.get("password", "")),
                                     auth["password"])
                  and totp_verify(auth["totp_secret"], str(body.get("code", ""))))
            if not ok:
                return self._send_json({"error": "wrong password or code"}, 401)
            token = new_session()
            return self._send_json({"ok": True}, set_cookie=token)

        if route == "/api/logout":
            token = self._cookie_token()
            _sessions.pop(token, None)
            return self._send_json({"ok": True}, clear_cookie=True)

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
