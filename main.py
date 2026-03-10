#!/usr/bin/env python3
"""Telegram-бот для скачивания видео из TikTok, Instagram и Twitter."""
from __future__ import annotations

import asyncio
import logging
import os
import re

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode

from config import BOT_TOKEN, MAX_FILE_SIZE, ADMIN_ID
from downloader import download_tiktok, download_twitter, download_instagram, compress_video, cleanup
from db import track_user, increment_downloads, get_stats, get_all_users

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Regex паттерны — без захвата замыкающей пунктуации
TIKTOK_RE = re.compile(
    r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/[^\s,;!?)>\]\"']+"
)
TWITTER_RE = re.compile(
    r"https?://(?:www\.)?(?:twitter\.com|x\.com)/\w+/status/\d+[^\s,;!?)>\]\"']*"
)
INSTAGRAM_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|reels|p)/[\w-]+[^\s,;!?)>\]\"']*"
)

TELEGRAM_CAPTION_LIMIT = 1024


def format_number(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    track_user(message.from_user.id, message.from_user.username,
               message.from_user.first_name, message.from_user.last_name)
    await message.answer(
        "Привет! Отправь ссылку на видео, и я скачаю его для тебя.\n\n"
        "Поддерживаемые платформы:\n"
        "- TikTok (tiktok.com, vm.tiktok.com)\n"
        "- Instagram Reels (instagram.com/reel/...)\n"
        "- Twitter / X (x.com, twitter.com)\n\n"
        "/help - справка"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "Просто отправь ссылку на видео.\n"
        "Можно отправить несколько ссылок в одном сообщении.\n\n"
        "Поддерживаемые платформы:\n"
        "- TikTok — видео без водяного знака\n"
        "- Instagram Reels\n"
        "- Twitter / X\n\n"
        "Если видео больше 50 МБ — бот автоматически сожмёт его.\n"
        "Бот скачает видео и отправит его тебе."
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    s = get_stats()
    await message.answer(
        f"Пользователей: {s['total_users']}\n"
        f"Загрузок: {s['total_downloads']}"
    )


@dp.message(Command("users"))
async def cmd_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    users = get_all_users()
    if not users:
        await message.answer("Пользователей пока нет.")
        return

    lines = []
    for i, u in enumerate(users, 1):
        name = u["first_name"] or ""
        if u["last_name"]:
            name += f" {u['last_name']}"
        username = f" @{u['username']}" if u["username"] else ""
        lines.append(f"{i}. {name}{username} | ID: {u['user_id']} | Загрузок: {u['downloads']}")

    text = "\n".join(lines)
    # Telegram лимит 4096 символов
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        await message.answer(chunk)


@dp.message(F.text)
async def handle_message(message: Message):
    track_user(message.from_user.id, message.from_user.username,
               message.from_user.first_name, message.from_user.last_name)

    # Собираем все ссылки с указанием платформы
    links = []
    for url in TIKTOK_RE.findall(message.text):
        links.append(("tiktok", url))
    for url in TWITTER_RE.findall(message.text):
        links.append(("twitter", url))
    for url in INSTAGRAM_RE.findall(message.text):
        links.append(("instagram", url))

    if not links:
        await message.answer("Отправь ссылку на видео (TikTok, Instagram Reels или Twitter/X).")
        return

    platform_names = {
        "tiktok": "TikTok",
        "twitter": "Twitter/X",
        "instagram": "Instagram",
    }

    for platform, url in links:
        name = platform_names.get(platform, "")
        status = await message.answer(f"⏳ Скачиваю с {name}...")

        try:
            if platform == "tiktok":
                result = await download_tiktok(url)
            elif platform == "twitter":
                result = await download_twitter(url)
            else:
                result = await download_instagram(url)
        except RuntimeError as e:
            await status.edit_text(f"❌ Ошибка: {e}")
            continue
        except Exception as e:
            log.exception("Неожиданная ошибка загрузки: %s", e)
            await status.edit_text("❌ Ошибка при скачивании. Попробуй позже.")
            continue

        if not result:
            await status.edit_text("❌ Не удалось скачать видео.")
            continue

        path = result["path"]

        # Автоматическое сжатие, если файл больше лимита Telegram (50 МБ)
        try:
            file_size = os.path.getsize(path)
            if file_size > MAX_FILE_SIZE:
                size_mb = file_size / (1024 * 1024)
                await status.edit_text(f"🗜 Видео {size_mb:.0f} МБ — сжимаю...")
                path = await compress_video(path)
                result["path"] = path
        except RuntimeError as e:
            await status.edit_text(f"❌ Ошибка: {e}")
            cleanup(result["path"])
            continue
        except Exception as e:
            log.exception("Ошибка сжатия: %s", e)
            await status.edit_text("❌ Не удалось сжать видео.")
            cleanup(result["path"])
            continue

        # Формируем подпись
        caption_parts = []
        if result.get("author"):
            author_line = result["author"]
            if result.get("author_id"):
                author_line += f" (@{result['author_id']})"
            caption_parts.append(author_line)
        if result.get("title"):
            caption_parts.append(result["title"][:200])

        stats_parts = []
        if result.get("views"):
            stats_parts.append(f"👁 {format_number(result['views'])}")
        if result.get("likes"):
            stats_parts.append(f"❤️ {format_number(result['likes'])}")
        if stats_parts:
            caption_parts.append(" | ".join(stats_parts))

        caption = "\n".join(caption_parts) if caption_parts else None

        # Обрезаем подпись до лимита Telegram (1024 символа)
        if caption and len(caption) > TELEGRAM_CAPTION_LIMIT:
            caption = caption[:TELEGRAM_CAPTION_LIMIT - 3] + "..."

        try:
            video_file = FSInputFile(path)
            await message.answer_video(
                video=video_file,
                caption=caption,
            )
            await status.delete()
            increment_downloads(message.from_user.id)
        except Exception as e:
            log.error("Ошибка отправки: %s", e)
            # Fallback: попробуем как документ
            try:
                video_file = FSInputFile(path)
                await message.answer_document(
                    document=video_file,
                    caption=caption,
                )
                await status.delete()
                increment_downloads(message.from_user.id)
            except Exception as e2:
                log.error("Ошибка отправки документом: %s", e2)
                await status.edit_text("❌ Не удалось отправить видео.")
        finally:
            cleanup(path)


async def main():
    log.info("Бот запущен")
    try:
        await bot.send_message(
            ADMIN_ID,
            "✅ Бот перезапущен!\n\n"
            "Что нового:\n"
            "• Автоматическое сжатие видео > 50 МБ\n"
            "• Улучшена стабильность загрузки\n"
            "• Исправлены ошибки"
        )
    except Exception as e:
        log.warning("Не удалось отправить уведомление админу: %s", e)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
