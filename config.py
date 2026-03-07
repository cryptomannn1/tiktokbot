import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip().strip("'\"") or "8781844883:AAFtONpT23t45GpUuFd3AbypSNWQESh_O3Y"
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB лимит Telegram
ADMIN_ID = 516972810  # @cryptomannn
