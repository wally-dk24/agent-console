# Agent Console - self-hosted ops console (stdlib-only, RUN-free)
FROM python:3.12-alpine
COPY app.py /app/app.py
COPY static /app/static/
COPY README.md /app/README.md
VOLUME /data
EXPOSE 8080
USER 1000
ENTRYPOINT ["python3", "/app/app.py"]
