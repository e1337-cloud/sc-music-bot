import aiosqlite
from typing import Optional, Dict, Any
from config import DATABASE_PATH

async def init_db():
    """Инициализация базы данных и создание таблиц."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tracks_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id TEXT UNIQUE,
                permalink_url TEXT UNIQUE,
                telegram_file_id TEXT NOT NULL,
                title TEXT,
                artist TEXT,
                duration INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def get_cached_track_by_id(track_id: str) -> Optional[Dict[str, Any]]:
    """Получить закэшированный трек по track_id."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tracks_cache WHERE track_id = ?",
            (str(track_id),)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return None

async def get_cached_track_by_url(permalink_url: str) -> Optional[Dict[str, Any]]:
    """Получить закэшированный трек по permalink_url."""
    # Нормализуем URL (убираем query параметры, если есть)
    clean_url = permalink_url.split("?")[0].rstrip("/")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tracks_cache WHERE permalink_url LIKE ? OR permalink_url = ?",
            (f"{clean_url}%", clean_url)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return None

async def save_track_cache(
    track_id: str,
    permalink_url: str,
    telegram_file_id: str,
    title: str,
    artist: str,
    duration: int
):
    """Сохранить трек и его telegram_file_id в кэш."""
    clean_url = permalink_url.split("?")[0].rstrip("/")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO tracks_cache (track_id, permalink_url, telegram_file_id, title, artist, duration)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                telegram_file_id = excluded.telegram_file_id,
                title = excluded.title,
                artist = excluded.artist,
                duration = excluded.duration
        """, (str(track_id), clean_url, telegram_file_id, title, artist, duration))
        await db.commit()
