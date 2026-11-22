import aiosqlite
from typing import Optional, Dict, Any
from config import DATABASE_PATH

async def init_db():
    """Инициализация базы данных и создание таблиц."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tracks_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id TEXT UNIQUE NOT NULL,
                permalink_url TEXT NOT NULL,
                telegram_file_id TEXT NOT NULL,
                title TEXT,
                artist TEXT,
                duration INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Индекс для быстрого поиска по permalink_url
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tracks_permalink ON tracks_cache (permalink_url)
        """)
        await db.commit()

async def get_cached_track_by_id(track_id: str) -> Optional[Dict[str, Any]]:
    """Получить закэшированный трек по track_id."""
    if not track_id:
        return None
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tracks_cache WHERE track_id = ? LIMIT 1",
            (str(track_id),)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return None

async def get_cached_track_by_url(permalink_url: str) -> Optional[Dict[str, Any]]:
    """Получить закэшированный трек по permalink_url."""
    if not permalink_url:
        return None
    # Нормализуем URL (убираем query параметры, если есть)
    clean_url = permalink_url.split("?")[0].rstrip("/")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tracks_cache WHERE permalink_url = ? OR permalink_url LIKE ? LIMIT 1",
            (clean_url, f"{clean_url}%")
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
    if not track_id or not telegram_file_id:
        return
    clean_url = (permalink_url or "").split("?")[0].rstrip("/")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO tracks_cache (track_id, permalink_url, telegram_file_id, title, artist, duration)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                permalink_url = excluded.permalink_url,
                telegram_file_id = excluded.telegram_file_id,
                title = excluded.title,
                artist = excluded.artist,
                duration = excluded.duration
        """, (str(track_id), clean_url, telegram_file_id, title or "Untitled", artist or "Unknown Artist", int(duration or 0)))
        await db.commit()
