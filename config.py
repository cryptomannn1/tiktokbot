import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip().strip("'\"")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан! Укажи его в переменных окружения.")
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")

# Свой Bot API-сервер (telegram-bot-api в --local режиме) снимает лимит 50 МБ.
# Пример: http://telegram-bot-api.railway.internal:8081
TELEGRAM_API_URL = os.environ.get("TELEGRAM_API_URL", "").strip()
if TELEGRAM_API_URL:
    MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2 ГБ лимит локального Bot API
else:
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB лимит облачного Bot API
ADMIN_ID = 516972810  # @cryptomannn
