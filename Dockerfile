# Agent Console - self-hosted ops console (stdlib-only, RUN-free)
# Starts as root so the entrypoint can chown /data to PUID:PGID, then drops
# privileges before the app starts. The app itself never runs as root.
FROM python:3.12-alpine
COPY app.py /app/app.py
COPY entrypoint.py /app/entrypoint.py
COPY static /app/static/
COPY README.md /app/README.md
VOLUME /data
EXPOSE 8080
ENTRYPOINT ["python3", "/app/entrypoint.py"]
