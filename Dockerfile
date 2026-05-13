FROM python:3.11-slim-bookworm

# ── System packages ─────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        sonic-annotator \
    && rm -rf /var/lib/apt/lists/*

# Chordino / NNLS-Chroma Vamp plugin (best-effort – may not be in Debian repos)
RUN apt-get update \
    && (apt-get install -y --no-install-recommends vamp-plugin-nnls-chroma || true) \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps ─────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── App ─────────────────────────────────────────────────────────────────────
COPY main.py .

# ── Smoke tests (fail fast at build time if tools are broken) ───────────────
RUN python -c "import demucs; print('demucs OK')"
RUN sonic-annotator -v 2>&1 | head -2

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
