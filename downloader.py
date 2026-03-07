from __future__ import annotations

import os
import httpx
from config import DOWNLOAD_DIR


async def download_video(url: str) -> dict | None:
    """Скачать TikTok видео через API. Возвращает dict с path и info."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

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
        filepath = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp4")

        resp = await client.get(video_url)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(resp.content)

        return {
            "path": filepath,
            "title": video.get("title", ""),
            "author": video.get("author", {}).get("nickname", ""),
            "author_id": video.get("author", {}).get("unique_id", ""),
            "duration": video.get("duration", 0),
            "views": video.get("play_count", 0),
            "likes": video.get("digg_count", 0),
        }


def cleanup(path: str):
    """Удалить файл после отправки."""
    try:
        os.remove(path)
    except OSError:
        pass
