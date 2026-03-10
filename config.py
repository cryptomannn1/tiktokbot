import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip().strip("'\"")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан! Укажи его в переменных окружения.")
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB лимит Telegram
ADMIN_ID = 516972810  # @cryptomannn
