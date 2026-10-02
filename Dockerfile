# Agent Console - thin API client (stdlib-only, RUN-free)
# Proxies the web UI to the Agent Console Data API (Cloudflare Worker).
# No local data files, no volumes. API_BASE_URL and API_TOKEN are required.
# Starts as root to drop privileges to PUID:PGID; the app never runs as root.
FROM python:3.12-alpine
COPY app.py /app/app.py
COPY entrypoint.py /app/entrypoint.py
COPY static /app/static/
COPY hatch-egress-ca.crt /app/hatch-egress-ca.crt
COPY README.md /app/README.md
EXPOSE 8080
ENTRYPOINT ["python3", "/app/entrypoint.py"]
