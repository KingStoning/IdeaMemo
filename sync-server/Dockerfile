FROM python:3.13-alpine
WORKDIR /app
RUN addgroup -S -g 10001 memo && adduser -S -D -H -u 10001 -G memo memo && mkdir /data && chown memo:memo /data
COPY server.py .
USER memo
ENV PYTHONUNBUFFERED=1 SYNC_DB=/data/ideamemo.sqlite3
EXPOSE 8787
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=3)" || exit 1
CMD ["python", "server.py"]
