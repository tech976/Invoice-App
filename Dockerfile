# The application as a server runs it: a real process, a real disk, and the
# speech stack present. That is the shape voice entry needs and the shape a
# serverless host cannot give it, which is why this file exists alongside
# vercel.json rather than instead of it.
FROM python:3.12-slim

# poppler and tesseract are the OCR fallback's, for a bill whose text layer
# is unusable; libsndfile is soundfile's, reading the clip before the
# recogniser hears it. None ships as a wheel, so none arrives with pip.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies before source, so an edit to a template does not reinstall
# 280 MB of wheels.
COPY deploy/requirements-server.txt deploy/requirements-server.txt
RUN pip install --no-cache-dir -r deploy/requirements-server.txt

COPY . .

# The recogniser is baked into the image rather than fetched at boot:
# speech.py loads it with local_files_only, and a model downloaded on the
# first recording is a broker standing in a market waiting for 250 MB.
ENV SPEECH_MODEL=small
RUN python scripts/fetch_speech_model.py

# Not serverless: bills go on the disk, workers run in the background, and
# voice entry is served because this host can actually carry it.
ENV ENABLE_VOICE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
