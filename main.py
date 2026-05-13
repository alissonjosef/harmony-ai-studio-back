"""
Harmony AI – Audio Backend
Separate microservice that handles:
  POST /api/separate-stems  – Demucs stem separation
  POST /api/analyze-chords  – Chordino (sonic-annotator) chord detection
  GET  /api/health          – liveness + capability check
"""

import base64
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Harmony AI Audio Backend", version="1.0.0")

# Allow calls from the Node.js SSR server and the browser (for future direct use)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SeparateStemsRequest(BaseModel):
    base64: str
    mimeType: str = "audio/mpeg"
    fileName: Optional[str] = None
    modelName: Optional[str] = None


class AnalyzeChordsRequest(BaseModel):
    base64: str
    mimeType: str = "audio/wav"
    fileName: Optional[str] = None


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "demucs": _check_demucs(),
        "sonic_annotator": _check_sonic_annotator(),
    }


def _check_demucs() -> bool:
    try:
        import demucs  # noqa: F401
        return True
    except ImportError:
        return False


def _check_sonic_annotator() -> bool:
    try:
        result = subprocess.run(
            ["sonic-annotator", "-v"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Stem separation (Demucs)
# ---------------------------------------------------------------------------

@app.post("/api/separate-stems")
async def separate_stems(req: SeparateStemsRequest):
    model_name = req.modelName or os.environ.get("DEMUCS_MODEL", "htdemucs")
    demucs_ext = os.environ.get("DEMUCS_EXT", "mp3")
    mp3_bitrate = os.environ.get("DEMUCS_MP3_BITRATE", "320")
    timeout_sec = int(os.environ.get("DEMUCS_TIMEOUT_SEC", "720"))

    try:
        audio_bytes = base64.b64decode(req.base64)
    except Exception:
        raise HTTPException(status_code=400, detail="base64 inválido")

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", req.fileName or "input")
    input_ext = _ext_from_mime(req.mimeType)

    with tempfile.TemporaryDirectory(prefix="demucs-") as tmp:
        input_path = os.path.join(tmp, f"{safe_name}.{input_ext}")
        output_dir = os.path.join(tmp, "out")
        os.makedirs(output_dir, exist_ok=True)

        with open(input_path, "wb") as fh:
            fh.write(audio_bytes)

        cmd = [
            "python", "-m", "demucs.separate",
            "-n", model_name,
            "-o", output_dir,
        ]
        if demucs_ext == "mp3":
            cmd += ["--mp3", "--mp3-bitrate", mp3_bitrate]
        elif demucs_ext == "flac":
            cmd += ["--flac"]
        cmd.append(input_path)

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Demucs excedeu tempo limite")

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise HTTPException(status_code=500, detail=f"Demucs falhou: {stderr[:500]}")

        stems = []
        stem_order = ["vocals", "drums", "bass", "guitar", "keys", "other"]

        for root, _dirs, files in os.walk(output_dir):
            for fname in files:
                lower = fname.lower()
                if not any(lower.endswith(ext) for ext in (".wav", ".mp3", ".flac", ".ogg")):
                    continue
                stem_key = Path(fname).stem.lower()
                if not any(k in stem_key for k in ("vocals", "drums", "bass", "other", "guitar", "piano", "keys")):
                    continue
                full_path = os.path.join(root, fname)
                with open(full_path, "rb") as fh:
                    data = fh.read()
                mime = "audio/mpeg" if demucs_ext == "mp3" else "audio/wav"
                stems.append({
                    "kind": _map_stem_kind(stem_key),
                    "audioUrl": f"data:{mime};base64,{base64.b64encode(data).decode()}",
                })

        if not stems:
            raise HTTPException(status_code=500, detail="Demucs não gerou stems reconhecíveis")

        stems.sort(key=lambda s: stem_order.index(s["kind"]) if s["kind"] in stem_order else 99)
        return {"stems": stems, "error": None}


def _map_stem_kind(name: str) -> str:
    if "vocal" in name:
        return "vocals"
    if "drum" in name:
        return "drums"
    if "bass" in name:
        return "bass"
    if "piano" in name or "key" in name:
        return "keys"
    if "guitar" in name:
        return "guitar"
    return "other"


# ---------------------------------------------------------------------------
# Chord analysis (sonic-annotator + Chordino)
# ---------------------------------------------------------------------------

@app.post("/api/analyze-chords")
async def analyze_chords(req: AnalyzeChordsRequest):
    chordino_bin = os.environ.get("CHORDINO_BIN", "sonic-annotator")
    chordino_plugin = os.environ.get("CHORDINO_PLUGIN", "vamp:nnls-chroma:chordino:chord")
    timeout_sec = int(os.environ.get("CHORDINO_TIMEOUT_SEC", "180"))

    try:
        audio_bytes = base64.b64decode(req.base64)
    except Exception:
        raise HTTPException(status_code=400, detail="base64 inválido")

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", req.fileName or "input")
    input_ext = _ext_from_mime(req.mimeType)

    with tempfile.TemporaryDirectory(prefix="chordino-") as tmp:
        input_path = os.path.join(tmp, f"{safe_name}.{input_ext}")
        output_dir = os.path.join(tmp, "out")
        os.makedirs(output_dir, exist_ok=True)

        with open(input_path, "wb") as fh:
            fh.write(audio_bytes)

        cmd = [
            chordino_bin,
            "-d", chordino_plugin,
            "-w", "csv",
            "--csv-force",
            "-o", output_dir,
            input_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Chordino excedeu tempo limite")
        except FileNotFoundError:
            raise HTTPException(
                status_code=503,
                detail=f"sonic-annotator não encontrado ({chordino_bin}). Instale no container.",
            )

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise HTTPException(status_code=500, detail=f"Chordino falhou: {stderr[:500]}")

        # Find the CSV output
        csv_content: Optional[str] = None
        for root, _dirs, files in os.walk(output_dir):
            for fname in files:
                if fname.lower().endswith(".csv"):
                    with open(os.path.join(root, fname), "r", encoding="utf-8") as fh:
                        csv_content = fh.read()
                    break
            if csv_content is not None:
                break

        if csv_content is None:
            raise HTTPException(status_code=500, detail="Chordino não produziu saída CSV")

        chords = _parse_chordino_csv(csv_content)
        if not chords:
            raise HTTPException(status_code=500, detail="Chordino não detectou acordes reconhecíveis")

        return {"chords": chords, "error": None}


# ---------------------------------------------------------------------------
# CSV + chord helpers
# ---------------------------------------------------------------------------

def _parse_chordino_csv(text: str) -> list:
    chords = []
    last_name = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = _parse_csv_line(line)
        nums = [float(c) for c in cols if _is_float(c)]
        if not nums:
            continue
        time = nums[0]
        label = next((c for c in reversed(cols) if c and not _is_float(c)), "")
        name = _normalize_chord(label)
        if not name or name == last_name:
            continue
        chords.append({"time": time, "name": name, "confidence": 0.88})
        last_name = name
    return chords


def _parse_csv_line(line: str) -> list:
    cols, token, in_quotes = [], "", False
    for ch in line:
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "," and not in_quotes:
            cols.append(token.strip())
            token = ""
        else:
            token += ch
    cols.append(token.strip())
    return cols


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _normalize_chord(label: str) -> Optional[str]:
    s = (label or "").strip().strip('"').strip()
    if not s:
        return None
    s = re.sub(r'\s+', '', s)
    s = s.replace("N/A", "N")
    s = re.sub(r':hdim7', 'm7b5', s, flags=re.IGNORECASE)
    s = re.sub(r':maj7', 'maj7', s, flags=re.IGNORECASE)
    s = re.sub(r':min7', 'm7', s, flags=re.IGNORECASE)
    s = re.sub(r':min', 'm', s, flags=re.IGNORECASE)
    s = re.sub(r':maj', '', s, flags=re.IGNORECASE)
    s = re.sub(r':sus4', 'sus4', s, flags=re.IGNORECASE)
    s = re.sub(r':sus2', 'sus2', s, flags=re.IGNORECASE)
    s = re.sub(r':aug', 'aug', s, flags=re.IGNORECASE)
    s = re.sub(r':dim', 'dim', s, flags=re.IGNORECASE)
    s = re.sub(r':7', '7', s, flags=re.IGNORECASE)
    s = s.replace('/', '').replace(',', '').rstrip('.')
    if re.match(r'^(N|X|silence|none)$', s, flags=re.IGNORECASE):
        return None
    m = re.match(r'^([A-G][#b]?)(.*)$', s)
    if not m:
        return None
    return m.group(1).upper() + m.group(2)


def _ext_from_mime(mime: str) -> str:
    mime = mime.lower()
    if "mpeg" in mime or "mp3" in mime:
        return "mp3"
    if "wav" in mime:
        return "wav"
    if "ogg" in mime:
        return "ogg"
    if "flac" in mime:
        return "flac"
    if "aac" in mime:
        return "aac"
    return "mp3"
