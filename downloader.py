from __future__ import annotations

import os
import re
import json
import uuid
import shutil
import asyncio
import logging
import httpx
from pathlib import Path
from config import DOWNLOAD_DIR, MAX_FILE_SIZE

log = logging.getLogger(__name__)


def _resolve_binary(name: str) -> str:
    """Найти бинарник в PATH или в типичных местах на macOS/Linux."""
    found = shutil.which(name)
    if found:
        return found
    candidates = [
        os.path.expanduser(f"~/bin/{name}"),
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/usr/bin/{name}",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return name  # fallback — subprocess кинет ошибку с понятным сообщением


FFMPEG = _resolve_binary("ffmpeg")
FFPROBE = _resolve_binary("ffprobe")
YTDLP = _resolve_binary("yt-dlp")

DOWNLOAD_TIMEOUT = 120
COMPRESS_TIMEOUT = 300  # 5 минут на сжатие
MAX_DOWNLOAD_SIZE = 500 * 1024 * 1024  # 500 MB — максимум для скачивания (потом сожмём)


def _safe_remove(path: str):
    """Безопасно удалить файл."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


async def _ffprobe(filepath: str) -> dict:
    """Получить метаданные видео через ffprobe (формат + первый видеопоток)."""
    cmd = [
        FFPROBE, "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        filepath,
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return {}
    try:
        return json.loads(stdout.decode())
    except (json.JSONDecodeError, ValueError):
        return {}


async def _get_video_duration(filepath: str) -> float:
    """Получить длительность видео через ffprobe."""
    data = await _ffprobe(filepath)
    try:
        return float(data.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        return 0.0


# Кодеки и pix_fmt, которые плеер Telegram рендерит с артефактами.
_TELEGRAM_SAFE_CODECS = {"h264", "avc1"}
_TELEGRAM_SAFE_PIX_FMTS = {"yuv420p", "yuvj420p"}


async def ensure_telegram_compatible(filepath: str) -> str:
    """Если видео в HEVC/VP9/10-bit — перекодировать в H.264 yuv420p.

    Без этого Telegram на некоторых клиентах показывает полосатые артефакты.
    """
    data = await _ffprobe(filepath)
    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    if not video_stream:
        return filepath

    codec = (video_stream.get("codec_name") or "").lower()
    pix_fmt = (video_stream.get("pix_fmt") or "").lower()

    if codec in _TELEGRAM_SAFE_CODECS and pix_fmt in _TELEGRAM_SAFE_PIX_FMTS:
        return filepath

    if not codec or not pix_fmt:
        file_size = os.path.getsize(filepath) if os.path.exists(filepath) else -1
        log.warning(
            "ffprobe: пустые поля кодека. stream=%s size=%d",
            video_stream, file_size,
        )

    output = filepath.rsplit(".", 1)[0] + "_h264.mp4"
    cmd = [
        FFMPEG, "-y", "-i", filepath,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output,
    ]

    log.info("Перекодирую в H.264: %s (codec=%s pix_fmt=%s)", filepath, codec, pix_fmt)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=COMPRESS_TIMEOUT
        )
    except asyncio.TimeoutError:
        process.kill()
        _safe_remove(output)
        log.warning("Перекодирование превысило таймаут — отдаю оригинал")
        return filepath

    if process.returncode != 0 or not os.path.exists(output):
        log.error(
            "ffmpeg ошибка перекодирования (rc=%s): %s",
            process.returncode,
            stderr.decode("utf-8", errors="replace")[-3000:],
        )
        _safe_remove(output)
        # Лучше отдать оригинал (возможно с артефактами), чем вернуть ошибку.
        return filepath

    _safe_remove(filepath)
    return output


async def compress_video(filepath: str, target_size: int = MAX_FILE_SIZE) -> str:
    """Сжать видео через ffmpeg до target_size байт.

    Возвращает путь к сжатому файлу.
    Raises RuntimeError если не удалось сжать.
    """
    file_size = os.path.getsize(filepath)
    if file_size <= target_size:
        return filepath

    duration = await _get_video_duration(filepath)
    if duration <= 0:
        raise RuntimeError("Не удалось определить длительность видео для сжатия")

    # Целевой битрейт: (target_size * 8 * 0.90) / duration
    # 90% от лимита — оставляем запас
    audio_bitrate = 128_000  # 128 kbps для аудио
    target_total_bitrate = int((target_size * 8 * 0.90) / duration)
    video_bitrate = max(target_total_bitrate - audio_bitrate, 200_000)

    # Если битрейт слишком низкий — качество будет ужасным
    if video_bitrate < 200_000:
        raise RuntimeError("Видео слишком длинное для сжатия до 50 МБ")

    output = filepath.rsplit(".", 1)[0] + "_compressed.mp4"

    cmd = [
        FFMPEG, "-y", "-i", filepath,
        "-c:v", "libx264", "-preset", "fast",
        "-b:v", str(video_bitrate),
        "-maxrate", str(int(video_bitrate * 1.5)),
        "-bufsize", str(int(video_bitrate * 2)),
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-vf", "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
        output,
    ]

    log.info("Сжимаю видео: %s (%.1f МБ → цель %.1f МБ, битрейт %d kbps)",
             filepath, file_size / (1024 * 1024), target_size / (1024 * 1024), video_bitrate // 1000)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=COMPRESS_TIMEOUT
        )
    except asyncio.TimeoutError:
        process.kill()
        _safe_remove(output)
        raise RuntimeError("Сжатие видео заняло слишком много времени")

    if process.returncode != 0:
        log.error("ffmpeg ошибка: %s", stderr.decode("utf-8", errors="replace")[:500])
        _safe_remove(output)
        raise RuntimeError("Не удалось сжать видео")

    if os.path.exists(output):
        compressed_size = os.path.getsize(output)
        if compressed_size <= target_size:
            _safe_remove(filepath)
            log.info("Сжатие успешно: %.1f МБ → %.1f МБ",
                     file_size / (1024 * 1024), compressed_size / (1024 * 1024))
            return output
        else:
            _safe_remove(output)
            raise RuntimeError("Видео слишком большое даже после сжатия")

    raise RuntimeError("Не удалось сжать видео")


async def _download_file(client: httpx.AsyncClient, video_url: str, filename: str) -> str:
    """Скачать файл по URL потоково с таймаутом."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    filepath = os.path.join(DOWNLOAD_DIR, filename)

    try:
        async with asyncio.timeout(DOWNLOAD_TIMEOUT):
            async with client.stream("GET", video_url) as resp:
                resp.raise_for_status()
                downloaded = 0
                with open(filepath, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        downloaded += len(chunk)
                        if downloaded > MAX_DOWNLOAD_SIZE:
                            raise RuntimeError("Видео слишком большое (больше 500 МБ)")
                        f.write(chunk)
    except TimeoutError:
        _safe_remove(filepath)
        raise RuntimeError("Загрузка заняла слишком много времени")
    except RuntimeError:
        _safe_remove(filepath)
        raise
    except Exception:
        _safe_remove(filepath)
        raise

    return filepath


async def _ytdlp_download(url: str, filename: str) -> dict:
    """Универсальный загрузчик через yt-dlp (subprocess)."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # UUID-префикс для уникальности (нет конфликтов при параллельных загрузках)
    unique_id = uuid.uuid4().hex[:8]
    safe_filename = f"{unique_id}_{filename}"
    filepath = os.path.join(DOWNLOAD_DIR, safe_filename)
    info_path = filepath + ".info.json"

    cmd = [
        YTDLP,
        "--no-warnings",
        "--no-playlist",
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

    def _cleanup_partial():
        """Удалить все файлы с нашим UUID-префиксом."""
        try:
            for f in Path(DOWNLOAD_DIR).iterdir():
                if f.name.startswith(unique_id):
                    _safe_remove(str(f))
        except OSError:
            pass

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=DOWNLOAD_TIMEOUT
        )
    except asyncio.TimeoutError:
        process.kill()
        _cleanup_partial()
        raise RuntimeError("Загрузка заняла слишком много времени")

    if process.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace")
        log.error("yt-dlp ошибка: %s", error_msg[:300])
        _cleanup_partial()
        raise RuntimeError("Не удалось скачать видео")

    # Ищем скачанный файл по UUID-префиксу (безопасно от race condition)
    video_file = None
    for f in Path(DOWNLOAD_DIR).iterdir():
        if f.name.startswith(unique_id) and f.suffix in (".mp4", ".webm", ".mkv"):
            video_file = f
            break

    if not video_file or not video_file.exists():
        _cleanup_partial()
        raise RuntimeError("Видео файл не найден после загрузки")

    file_size = video_file.stat().st_size
    if file_size > MAX_DOWNLOAD_SIZE:
        _cleanup_partial()
        raise RuntimeError("Видео слишком большое (больше 500 МБ)")

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
            _safe_remove(str(info_file))

    # Убираем info.json с другими возможными расширениями
    for f in Path(DOWNLOAD_DIR).iterdir():
        if f.name.startswith(unique_id) and f.suffix == ".json":
            _safe_remove(str(f))

    metadata["path"] = str(video_file)
    return metadata


async def download_tiktok(url: str) -> dict | None:
    """Скачать TikTok видео через tikwm API."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(
            "https://www.tikwm.com/api/",
            params={"url": url},
        )
        data = resp.json()

        if data.get("code") != 0 or not data.get("data"):
            raise RuntimeError(data.get("msg", "Не удалось получить видео"))

        video = data["data"]
        # hdplay с tikwm часто отдаёт ByteVC2 (bvc2) — TikTok-проприетарный кодек,
        # который не декодирует ни ffmpeg, ни Telegram. play = обычный H.264.
        video_url = video.get("play") or video.get("hdplay")
        if not video_url:
            raise RuntimeError("Нет ссылки на видео")

        if video_url.startswith("//"):
            video_url = "https:" + video_url

        video_id = video.get("id", uuid.uuid4().hex[:12])
        filepath = await _download_file(client, video_url, f"tt_{video_id}.mp4")

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
    _safe_remove(path)
