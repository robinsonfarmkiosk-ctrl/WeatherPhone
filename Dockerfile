FROM python:3.12-slim

# Install ffmpeg (for 8kHz mono WAV conversion), espeak-ng (offline TTS fallback),
# and baresip (headless SIP user agent)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    espeak-ng \
    baresip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY nws_sip_alerter.py .
COPY contacts.csv .

RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1
ENV STATE_DIR=/data

CMD ["python", "nws_sip_alerter.py"]
