# ── Stage 1: compile nnls-chroma Vamp plugin ─────────────────────────────────
# code.soundsoftware.ac.uk is unreachable; build from GitHub source instead
FROM python:3.11-slim-bookworm AS plugin-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        g++ \
        make \
        git \
        pkg-config \
        libboost-dev \
    && rm -rf /var/lib/apt/lists/*

# Build vamp-plugin-sdk with static libs only so the plugin .so is self-contained
# (no libvamp-sdk.so runtime dependency in the final image)
RUN git clone --depth 1 https://github.com/vamp-plugins/vamp-plugin-sdk.git /build/vamp-plugin-sdk \
    && cd /build/vamp-plugin-sdk \
    && ./configure --disable-shared --disable-programs \
    && make

# Build nnls-chroma; the static libvamp-sdk.a gets linked directly into the .so
RUN git clone --depth 1 https://github.com/c4dm/nnls-chroma.git /build/nnls-chroma \
    && cd /build/nnls-chroma \
    && make -f Makefile.linux VAMP_SDK_DIR=/build/vamp-plugin-sdk

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

# ── System packages ─────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── sonic-annotator v1.7 static binary ──────────────────────────────────────
RUN curl -fsSL "https://github.com/sonic-visualiser/sonic-annotator/releases/download/sonic-annotator-1.7/sonic-annotator-1.7.0-linux64-static.tar.gz" \
       -o /tmp/sonic-annotator.tar.gz \
    && tar -xzf /tmp/sonic-annotator.tar.gz -C /tmp \
    && find /tmp -name "sonic-annotator" -type f -exec install -m 755 {} /usr/local/bin/sonic-annotator \; \
    && rm -rf /tmp/sonic-annotator*

# ── nnls-chroma Vamp plugin (compiled in stage 1) ────────────────────────────
RUN mkdir -p /usr/local/lib/vamp
COPY --from=plugin-builder /build/nnls-chroma/nnls-chroma.so /usr/local/lib/vamp/

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
