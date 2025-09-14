FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依存関係
RUN pip install --no-cache-dir requests

# アプリ本体
COPY app.py /app/app.py

# データ用ディレクトリ（last_check, notified_tickets, renotify_tickets, completed_tickets）
RUN mkdir -p /app/data

ENV LAST_CHECK_FILE=/app/data/last_check.txt \
    NOTIFIED_TICKETS_FILE=/app/data/notified_tickets.txt \
    RENOTIFY_TICKETS_FILE=/app/data/renotify_tickets.txt \
    COMPLETED_TICKETS_FILE=/app/data/completed_tickets.txt \
    POLLING_INTERVAL=10 \
    RENOTIFY_INTERVAL_SECONDS=20 \
    NOTIFY_TRACKER_IDS="28,31,33" \
    USER_MAPPING_JSON='{"Redmine上の担当者名": "SlackのメンバーID"}'

CMD ["python", "/app/app.py"]

