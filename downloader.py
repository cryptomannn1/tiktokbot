from __future__ import annotations

import os
import re
import json
import uuid
import asyncio
import logging
import httpx
from pathlib import Path
from config import DOWNLOAD_DIR, MAX_FILE_SIZE

log = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 90


async def _download_file(client: httpx.AsyncClient, video_url: str, filename: str) -> str:
    """Скачать файл по URL потоково с общим таймаутом."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    filepath = os.path.join(DOWNLOAD_DIR, filename)

    async with asyncio.timeout(DOWNLOAD_TIMEOUT):
        async with client.stream("GET", video_url) as resp:
            resp.raise_for_status()
            downloaded = 0
            with open(filepath, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    downloaded += len(chunk)
                    if downloaded > MAX_FILE_SIZE:
                        raise RuntimeError("Видео слишком большое (больше 50 МБ)")
                    f.write(chunk)

    return filepath


async def _ytdlp_download(url: str, filename: str) -> dict:
    """Универсальный загрузчик через yt-dlp (subprocess)."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    info_path = filepath + ".info.json"

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        "--max-filesize", "50m",
        "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--no-write-playlist-metafiles",
        "-o", filepath,
        url,
    ]

    log.info("yt-dlp: %s", url)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=DOWNLOAD_TIMEOUT
        )
    except asyncio.TimeoutError:
        process.kill()
        raise RuntimeError("Загрузка заняла слишком много времени")

    if process.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace")
        log.error("yt-dlp ошибка: %s", error_msg[:300])
        raise RuntimeError("Не удалось скачать видео")

    # Ищем скачанный файл
    video_file = None
    for f in Path(DOWNLOAD_DIR).iterdir():
        if f.name.startswith(Path(filename).stem) and f.suffix in (".mp4", ".webm", ".mkv"):
            video_file = f
            break

    if not video_file or not video_file.exists():
        raise RuntimeError("Видео файл не найден после загрузки")

    file_size = video_file.stat().st_size
    if file_size > MAX_FILE_SIZE:
        video_file.unlink(missing_ok=True)
        raise RuntimeError("Видео слишком большое (больше 50 МБ)")

    # Метаданные
    metadata = {"title": "", "author": "", "author_id": "", "duration": 0, "views": 0, "likes": 0}
    info_file = Path(info_path)
    if info_file.exists():
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            metadata["title"] = data.get("title", "")[:200]
            metadata["author"] = data.get("uploader", data.get("creator", ""))
            metadata["author_id"] = data.get("uploader_id", "")
            metadata["duration"] = data.get("duration", 0)
            metadata["views"] = data.get("view_count", 0)
            metadata["likes"] = data.get("like_count", 0)
        except Exception:
            pass
        finally:
            info_file.unlink(missing_ok=True)

    metadata["path"] = str(video_file)
    return metadata


async def download_tiktok(url: str) -> dict | None:
    """Скачать TikTok видео через tikwm API."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(
            "https://www.tikwm.com/api/",
            params={"url": url, "hd": 1},
        )
        data = resp.json()

        if data.get("code") != 0 or not data.get("data"):
            raise RuntimeError(data.get("msg", "Не удалось получить видео"))

        video = data["data"]
        video_url = video.get("hdplay") or video.get("play")
        if not video_url:
            raise RuntimeError("Нет ссылки на видео")

        if video_url.startswith("//"):
            video_url = "https:" + video_url

        video_id = video.get("id", "video")
        filepath = await _download_file(client, video_url, f"{video_id}.mp4")

        return {
            "path": filepath,
            "title": video.get("title", ""),
            "author": video.get("author", {}).get("nickname", ""),
            "author_id": video.get("author", {}).get("unique_id", ""),
            "duration": video.get("duration", 0),
            "views": video.get("play_count", 0),
            "likes": video.get("digg_count", 0),
        }


async def download_twitter(url: str) -> dict | None:
    """Скачать Twitter/X видео через yt-dlp."""
    match = re.search(r"(?:twitter\.com|x\.com)/(\w+)/status/(\d+)", url)
    if not match:
        raise RuntimeError("Некорректная ссылка на Twitter/X")
    status_id = match.group(2)
    return await _ytdlp_download(url, f"tw_{status_id}.mp4")


async def download_instagram(url: str) -> dict | None:
    """Скачать Instagram Reels через yt-dlp."""
    file_id = uuid.uuid4().hex[:12]
    return await _ytdlp_download(url, f"ig_{file_id}.mp4")


def cleanup(path: str):
    """Удалить файл после отправки."""
    try:
        os.remove(path)
    except OSError:
        pass
