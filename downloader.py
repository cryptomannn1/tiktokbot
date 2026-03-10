from __future__ import annotations

import os
import re
import uuid
import asyncio
import httpx
from config import DOWNLOAD_DIR, MAX_FILE_SIZE

# Общий таймаут на всю загрузку (секунды)
DOWNLOAD_TIMEOUT = 60


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
    """Скачать Twitter/X видео через fxtwitter API."""
    # Преобразуем URL в API-запрос fxtwitter
    match = re.search(r"(?:twitter\.com|x\.com)/(\w+)/status/(\d+)", url)
    if not match:
        raise RuntimeError("Некорректная ссылка на Twitter/X")

    user, status_id = match.group(1), match.group(2)
    api_url = f"https://api.fxtwitter.com/{user}/status/{status_id}"

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(api_url)
        data = resp.json()

        tweet = data.get("tweet")
        if not tweet:
            raise RuntimeError("Не удалось получить твит")

        media = tweet.get("media")
        if not media or not media.get("videos"):
            raise RuntimeError("В этом твите нет видео")

        best_video = media["videos"][0]
        video_url = best_video.get("url")
        if not video_url:
            raise RuntimeError("Нет ссылки на видео")

        filepath = await _download_file(client, video_url, f"tw_{status_id}.mp4")

        return {
            "path": filepath,
            "title": tweet.get("text", "")[:200],
            "author": tweet.get("author", {}).get("name", ""),
            "author_id": tweet.get("author", {}).get("screen_name", ""),
            "duration": best_video.get("duration", 0),
            "views": tweet.get("views", 0),
            "likes": tweet.get("likes", 0),
        }


async def download_instagram(url: str) -> dict | None:
    """Скачать Instagram Reels через API."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(
            "https://api.saveig.app/api/v1/media",
            params={"url": url},
        )
        data = resp.json()

        if not data.get("data"):
            raise RuntimeError("Не удалось получить видео из Instagram")

        items = data["data"]
        # Ищем первый видео-элемент
        video_item = None
        for item in items:
            if item.get("type") == "video" or item.get("url", "").endswith(".mp4"):
                video_item = item
                break

        if not video_item:
            raise RuntimeError("Видео не найдено в этом посте")

        video_url = video_item.get("url")
        if not video_url:
            raise RuntimeError("Нет ссылки на видео")

        file_id = uuid.uuid4().hex[:12]
        filepath = await _download_file(client, video_url, f"ig_{file_id}.mp4")

        return {
            "path": filepath,
            "title": "",
            "author": "",
            "author_id": "",
            "duration": 0,
            "views": 0,
            "likes": 0,
        }


def cleanup(path: str):
    """Удалить файл после отправки."""
    try:
        os.remove(path)
    except OSError:
        pass
