"""Audio/video: local transcription (faster-whisper) and slide-change keyframes (PyAV)."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path

import numpy as np

from study.ingest.base import Chunk, Extracted, Image

AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".webm"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".m4v"}
MEDIA_EXT = AUDIO_EXT | VIDEO_EXT

WINDOW_S = 75.0   # target chunk length
OVERLAP_S = 10.0  # context carried into the next chunk
GPU_MODEL = "large-v3-turbo"
CPU_MODEL = "medium"

_cuda_loaded = False


def _preload_cuda_libs() -> None:
    """Load cuBLAS/cuDNN from the nvidia-* wheels so ctranslate2 finds them without LD_LIBRARY_PATH."""
    global _cuda_loaded
    if _cuda_loaded:
        return
    _cuda_loaded = True
    try:
        import nvidia  # namespace package from nvidia-cublas-cu12 / nvidia-cudnn-cu12
    except ImportError:
        return
    for base in nvidia.__path__:
        for sub in ("cublas", "cudnn"):
            libdir = Path(base) / sub / "lib"
            if not libdir.is_dir():
                continue
            for lib in sorted(libdir.glob("*.so*")):
                try:
                    ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def _load_model(model: str | None, device: str):
    from faster_whisper import WhisperModel
    import ctranslate2

    if device in ("auto", "cuda"):
        _preload_cuda_libs()
        if ctranslate2.get_cuda_device_count() > 0:
            try:
                return WhisperModel(model or GPU_MODEL, device="cuda", compute_type="float16"), "cuda"
            except Exception as e:  # missing/incompatible CUDA libs, OOM, ...
                if device == "cuda":
                    raise
                print(f"[transcribe] CUDA unavailable ({e}); falling back to CPU", file=sys.stderr)
    threads = max(1, (os.cpu_count() or 4) - 2)
    return WhisperModel(model or CPU_MODEL, device="cpu", compute_type="int8", cpu_threads=threads), "cpu"


def decode_audio(path: Path, sr: int = 16000) -> np.ndarray:
    """Decode any audio/video file to mono float32 at `sr` Hz with PyAV (no ffmpeg binary needed).

    faster-whisper's own decoder passes an argument removed in PyAV >= 15, so we decode ourselves.
    """
    import av

    resampler = av.AudioResampler(format="s16", layout="mono", rate=sr)
    parts = []
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                parts.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            parts.append(out.to_ndarray().reshape(-1))
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32) / 32768.0


def transcribe(path: Path, cache_file: Path, *, model: str | None = None, device: str = "auto",
               language: str | None = None, progress: bool = True) -> dict:
    """Transcribe (or load cached) -> {"segments": [[start, end, text], ...], "duration", "model", ...}."""
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    wm, used = _load_model(model, device)
    segments, info = wm.transcribe(decode_audio(path), language=language, vad_filter=True, beam_size=5,
                                   condition_on_previous_text=False)
    segs = []
    for s in segments:
        segs.append([round(s.start, 2), round(s.end, 2), s.text.strip()])
        if progress:
            print(f"\r[transcribe:{used}] {s.end / max(info.duration, 1):6.1%}", end="", file=sys.stderr)
    if progress:
        print(file=sys.stderr)
    out = {"segments": segs, "duration": info.duration, "language": info.language,
           "model": model or (GPU_MODEL if used == "cuda" else CPU_MODEL), "device": used}
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(out))
    return out


def chunk_segments(segs: list[list], window: float = WINDOW_S, overlap: float = OVERLAP_S) -> list[Chunk]:
    """Greedy ~`window`-second chunks on segment boundaries; each chunk re-includes the last ~`overlap` s."""
    chunks: list[Chunk] = []
    i, n = 0, len(segs)
    while i < n:
        j = i
        while j + 1 < n and segs[j + 1][1] - segs[i][0] <= window:
            j += 1
        text = " ".join(s[2] for s in segs[i:j + 1]).strip()
        if text:
            chunks.append(Chunk(segs[i][0], segs[j][1], text))
        if j + 1 >= n:
            break
        # next start: earliest segment within the overlap tail, but always make progress
        k = j + 1
        while k - 1 > i and segs[j][1] - segs[k - 1][0] <= overlap:
            k -= 1
        i = max(k, i + 1)
    return chunks


def _ahash(img, size: int = 16) -> np.ndarray:
    g = img.convert("L").resize((size, size))
    a = np.asarray(g, dtype=np.float32)
    return a > a.mean()


def extract_frames(path: Path, out_dir: Path, min_gap_s: float = 5.0, threshold: int = 24,
                   max_width: int = 1280) -> list[Image]:
    """Keyframes whose average-hash differs from the last kept frame (= slide changes)."""
    import av

    out_dir.mkdir(parents=True, exist_ok=True)
    kept: list[Image] = []
    last_hash, last_t = None, -1e9
    with av.open(str(path)) as container:
        if not container.streams.video:
            return []
        stream = container.streams.video[0]
        stream.codec_context.skip_frame = "NONKEY"
        for frame in container.decode(stream):
            if frame.time is None or frame.time - last_t < min_gap_s:
                continue
            img = frame.to_image()
            h = _ahash(img)
            if last_hash is not None and int((h != last_hash).sum()) < threshold:
                continue
            last_hash, last_t = h, frame.time
            if img.width > max_width:
                img = img.resize((max_width, int(img.height * max_width / img.width)))
            out = out_dir / f"t{int(frame.time):05d}.jpg"
            img.save(out, quality=85)
            kept.append(Image(round(frame.time, 1), "frame", out))
    return kept


def extract_media(path: Path, cache_dir: Path, sha: str, *, model=None, device="auto", language=None,
                  frames: bool = True) -> Extracted:
    tr = transcribe(path, cache_dir / "transcripts" / f"{sha}.json", model=model, device=device,
                    language=language)
    images = []
    if frames and path.suffix.lower() in VIDEO_EXT:
        images = extract_frames(path, cache_dir / "frames" / sha)
    meta = {k: tr[k] for k in ("language", "model", "device")}
    return Extracted("t", float(tr["duration"]), chunk_segments(tr["segments"]), images, meta)


def extract_subtitles(segs: list[tuple[float, float, str]]) -> Extracted:
    duration = max((b for _, b, _ in segs), default=0.0)
    return Extracted("t", duration, chunk_segments([list(s) for s in segs]), [], {"model": "subtitles"})
