FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BUTLER_STORAGE=firestore \
    BUTLER_MEDIA_STORAGE=gcs \
    BUTLER_MODEL=vertex \
    GOOGLE_CLOUD_LOCATION=asia-south1 \
    BUTLER_VERTEX_MODEL=gemini-3.5-flash \
    BUTLER_DATA_DIR=/tmp/butler \
    BUTLER_COOKIE_SECURE=1 \
    BUTLER_FFMPEG_PATH=/usr/bin/ffmpeg \
    BUTLER_FFPROBE_PATH=/usr/bin/ffprobe \
    BUTLER_FONT_PATH=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 butler

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
COPY data ./data
COPY run.py ./run.py
USER 10001
EXPOSE 8080
CMD ["python", "run.py"]
