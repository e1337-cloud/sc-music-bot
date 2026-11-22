import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения или .env файле!")

# Опциональный альтернативный сервер/зеркало Telegram Bot API (Cloudflare Worker)
CUSTOM_API_SERVER = os.getenv("CUSTOM_API_SERVER") or None

# Опциональный прокси для Telegram API (игнорируется, если задан CUSTOM_API_SERVER)
PROXY_URL = os.getenv("PROXY_URL") or None

DOWNLOADS_DIR = BASE_DIR / os.getenv("DOWNLOADS_DIR", "downloads")
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = BASE_DIR / os.getenv("DATABASE_PATH", "bot_cache.db")

# Поиск FFmpeg (системный или встроенный из imageio_ffmpeg)
FFMPEG_PATH = shutil.which("ffmpeg")
if not FFMPEG_PATH:
    try:
        import imageio_ffmpeg
        FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        FFMPEG_PATH = None
