FROM python:3.11-slim
WORKDIR /app
# Tesseract for orientation detection (the osd data comes with tesseract-ocr).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-heb \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml requirements.lock ./
COPY app ./app
# Exact versions from the lock file (scripts/lock_requirements.py); dev tools are not installed.
RUN pip install --no-cache-dir -c requirements.lock .
COPY form_types ./form_types
COPY data ./data
COPY prompts ./prompts
ENV CE_FORM_TYPES_DIR=/app/form_types \
    CE_SENDERS_PATH=/app/data/senders.json \
    CE_PROMPTS_DIR=/app/prompts \
    CE_TMP_DIR=/tmp/commitments-extractor
EXPOSE 8000
# Render (and similar hosts) assign the port in $PORT; 8000 locally.
# exec keeps uvicorn as PID 1 so it receives SIGTERM directly.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
