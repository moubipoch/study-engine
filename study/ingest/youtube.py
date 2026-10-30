"""YouTube (public videos only): download best audio with yt-dlp (no ffmpeg merge needed)."""

from __future__ import annotations

from pathlib import Path


def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def download_audio(url: str, out_dir: Path) -> tuple[Path, dict]:
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        target = Path(ydl.prepare_filename(info))
        if not target.exists():
            ydl.download([url])
    meta = {"url": url, "title": info.get("title"), "id": info.get("id"), "channel": info.get("channel"),
            "upload_date": info.get("upload_date")}
    return target, meta
