FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 radar && useradd -r -u 10001 -g radar radar \
    && mkdir /data && chown radar:radar /data
WORKDIR /app
COPY radar.py cloud.py healthcheck.py cycle.py strategy.py positions.py telegram_commands.py safety.py dingtalk.py alerts.py fast_lane.py evaluation.py ranking.py binance_rank.py ./
COPY data/research.json ./data/research.json
USER 10001:10001
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 RADAR_DATA=/data
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 CMD ["python", "healthcheck.py"]
CMD ["python", "cloud.py"]
