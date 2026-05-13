FROM python:3.11-slim-bookworm

# ── System packages ─────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        sonic-annotator \
        curl \
        bzip2 \
    && rm -rf /var/lib/apt/lists/*

# ── NNLS-Chroma / Chordino Vamp plugin (linux64 binary) ─────────────────────
RUN mkdir -p /usr/local/lib/vamp \
    && curl -fsSL "http://code.soundsoftware.ac.uk/attachments/download/1693/nnls-chroma-linux64-v1.1.tar.bz2" \
       -o /tmp/nnls-chroma.tar.bz2 \
    && tar -xjf /tmp/nnls-chroma.tar.bz2 -C /tmp \
    && find /tmp -name "*.so" -exec cp {} /usr/local/lib/vamp/ \; \
    && rm -rf /tmp/nnls-chroma*

# ── Python deps ─────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── App ─────────────────────────────────────────────────────────────────────
COPY main.py .

# ── Smoke tests (fail fast at build time if tools are broken) ───────────────
RUN python -c "import demucs; print('demucs OK')"
RUN sonic-annotator -v

# ── Runtime env defaults ────────────────────────────────────────────────────
ENV DEMUCS_MODEL=htdemucs \
    DEMUCS_EXT=mp3 \
    DEMUCS_MP3_BITRATE=320 \
    DEMUCS_TIMEOUT_SEC=720 \
    CHORDINO_BIN=sonic-annotator \
    CHORDINO_PLUGIN=vamp:nnls-chroma:chordino:chord \
    CHORDINO_TIMEOUT_SEC=180 \
    VAMP_PATH=/usr/lib/vamp:/usr/local/lib/vamp

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
