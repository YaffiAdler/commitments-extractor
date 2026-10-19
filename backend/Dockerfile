FROM python:3.11-slim
WORKDIR /app
# Tesseract for orientation detection (the osd data comes with tesseract-ocr).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-heb \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .
COPY form_types ./form_types
COPY data ./data
ENV CE_FORM_TYPES_DIR=/app/form_types \
    CE_SENDERS_PATH=/app/data/senders.json \
    CE_TMP_DIR=/tmp/commitments-extractor
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
