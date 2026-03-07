#!/usr/bin/env python3
"""Telegram-бот для скачивания TikTok видео."""
from __future__ import annotations

import asyncio
import logging
import os
import re

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode

from config import BOT_TOKEN, MAX_FILE_SIZE
from downloader import download_video, cleanup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

TIKTOK_RE = re.compile(
    r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/\S+"
)


def format_number(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Отправь ссылку на TikTok видео, и я скачаю его для тебя.\n\n"
        "Поддерживаемые форматы ссылок:\n"
        "- https://www.tiktok.com/@user/video/...\n"
        "- https://vm.tiktok.com/...\n"
        "- https://vt.tiktok.com/...\n\n"
        "/help - справка"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "Просто отправь ссылку на TikTok видео.\n"
        "Можно отправить несколько ссылок в одном сообщении.\n\n"
        "Бот скачает видео без водяного знака и отправит его тебе."
    )


@dp.message(F.text)
async def handle_message(message: Message):
    urls = TIKTOK_RE.findall(message.text)

    if not urls:
        await message.answer("Отправь ссылку на TikTok видео.")
        return

    for url in urls:
        status = await message.answer("Скачиваю...")

        try:
            result = await download_video(url)
        except RuntimeError as e:
            await status.edit_text(f"Ошибка: {e}")
            continue

        if not result:
            await status.edit_text("Не удалось скачать видео.")
            continue

        path = result["path"]
        file_size = os.path.getsize(path)

        if file_size > MAX_FILE_SIZE:
            await status.edit_text("Видео слишком большое (больше 50 МБ).")
            cleanup(path)
            continue

        caption_parts = []
        if result["author"]:
            caption_parts.append(f"{result['author']} (@{result['author_id']})")
        if result["title"]:
            caption_parts.append(result["title"][:200])

        stats = []
        if result["views"]:
            stats.append(f"Просмотры: {format_number(result['views'])}")
        if result["likes"]:
            stats.append(f"Лайки: {format_number(result['likes'])}")
        if stats:
            caption_parts.append(" | ".join(stats))

        caption = "\n".join(caption_parts) if caption_parts else None

        try:
            video_file = FSInputFile(path)
            await message.answer_document(
                document=video_file,
                caption=caption,
            )
            await status.delete()
        except Exception as e:
            log.error("Ошибка отправки: %s", e)
            await status.edit_text("Не удалось отправить видео.")
        finally:
            cleanup(path)


async def main():
    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
