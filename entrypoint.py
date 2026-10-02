#!/usr/bin/env python3
"""Entrypoint: start as root, fix /data ownership, drop to PUID/PGID, run app.

Env:
    PUID      target user id  (default 1000)
    PGID      target group id (default 1000)
    DATA_DIR  data directory  (default /data)

When the container is started as root (the default), the data directory is
recursively chown'ed to PUID:PGID so file ownership stays sane,
then privileges are dropped before the app starts. The app itself never runs
as root. If the container is started as a non-root user already, the
entrypoint just runs the app as-is.
"""

import os
import sys


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        print(f"entrypoint: ignoring invalid {name}, using {default}",
              flush=True)
        return default


def _chown_tree(path: str, uid: int, gid: int) -> None:
    """Recursively chown path to uid:gid; warn and continue on errors."""
    try:
        os.chown(path, uid, gid)
    except OSError as e:
        print(f"entrypoint: could not chown {path}: {e}", flush=True)
        return
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            p = os.path.join(root, name)
            try:
                if not os.path.islink(p):
                    os.chown(p, uid, gid)
            except OSError as e:
                print(f"entrypoint: could not chown {p}: {e}", flush=True)


def main() -> None:
    puid = _int_env("PUID", 1000)
    pgid = _int_env("PGID", 1000)
    data_dir = os.environ.get("DATA_DIR", "/data")

    if os.getuid() == 0:
        os.makedirs(data_dir, exist_ok=True)
        _chown_tree(data_dir, puid, pgid)
        try:
            os.setgid(pgid)
            os.setuid(puid)
        except OSError as e:
            print(f"entrypoint: could not drop to {puid}:{pgid}: {e}",
                  flush=True)
            sys.exit(1)
        print(f"entrypoint: running as UID {puid} GID {pgid}", flush=True)
    else:
        print(f"entrypoint: already UID {os.getuid()}, keeping it",
              flush=True)

    sys.path.insert(0, "/app")
    import app
    app.main()


if __name__ == "__main__":
    main()
