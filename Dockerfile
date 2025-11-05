FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN pip install --no-cache-dir requests slack-sdk
COPY app.py /app/app.py
RUN mkdir -p /app/data
ENV LAST_CHECK_FILE=/app/data/last_check.txt \
    NOTIFIED_TICKETS_FILE=/app/data/notified_tickets.txt \
    COMPLETED_TICKETS_FILE=/app/data/completed_tickets.txt \
    MESSAGE_MAPPING_FILE=/app/data/message_mapping.txt \
    TRACKER_MAPPING_FILE=/app/data/tracker_mapping.txt \
    CREATION_TIME_MAPPING_FILE=/app/data/creation_time_mapping.txt \
    PENDING_MESSAGE_MAPPING_FILE=/app/data/pending_message_mapping.txt \
    SLACK_COMPLETION_EMOJI=white_check_mark \
    SLACK_DELETION_EMOJI=wastebasket \
    POLLING_INTERVAL=10 \
    PENDING_NOTIFICATION_INTERVAL_SECONDS=3600 \
    NOTIFY_TRACKER_IDS="28,31,33" \
    NOTIFY_PROJECT_IDS="" \
    USER_MAPPING_JSON='<"Redmine上の担当者名": "SlackのメンバーID">'
CMD ["python", "/app/app.py"]

