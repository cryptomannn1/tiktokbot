from __future__ import annotations

import os
import yt_dlp
from config import DOWNLOAD_DIR


def download_video(url: str) -> dict | None:
    """Скачать видео. Возвращает dict с path и info или None."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    ydl_opts = {
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(id)s.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                base, _ = os.path.splitext(filename)
                filename = base + ".mp4"

            return {
                "path": filename,
                "title": info.get("title", ""),
                "author": info.get("uploader", ""),
                "author_id": info.get("uploader_id", ""),
                "duration": info.get("duration", 0),
                "views": info.get("view_count", 0),
                "likes": info.get("like_count", 0),
            }
    except Exception as e:
        raise RuntimeError(str(e))


def cleanup(path: str):
    """Удалить файл после отправки."""
    try:
        os.remove(path)
    except OSError:
        pass
