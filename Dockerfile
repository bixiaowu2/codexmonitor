FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && apt-get update && apt-get install -y --no-install-recommends xvfb xauth && rm -rf /var/lib/apt/lists/*

COPY x_monitor.py README.md CLOUD_DEPLOY.md .env.example routes.example.json routes.telegram.example.json ./
COPY x-monitor.service.example ./

COPY scripts/run-monitor.sh scripts/run-monitor.sh
CMD ["bash", "scripts/run-monitor.sh"]
