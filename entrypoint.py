#!/usr/bin/env python3
"""Entrypoint: start as root, drop to PUID/PGID, run the app.

The app is a thin API client — it holds no local data, so there is no
data directory to fix up. The container still starts as root by default
so privilege-dropping stays consistent, then the app never runs as root.

Env:
    PUID  target user id  (default 1000)
    PGID  target group id (default 1000)
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


def main() -> None:
    puid = _int_env("PUID", 1000)
    pgid = _int_env("PGID", 1000)

    if os.getuid() == 0:
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
