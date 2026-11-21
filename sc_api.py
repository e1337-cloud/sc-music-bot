import re
import asyncio
import logging
from typing import List, Dict, Any, Optional
import aiohttp

logger = logging.getLogger(__name__)

class SoundCloudAPI:
    """Асинхронный клиент для взаимодействия с SoundCloud v2 API через aiohttp."""

    BASE_URL = "https://api-v2.soundcloud.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    _client_id: Optional[str] = None
    _lock = asyncio.Lock()

    def __init__(self):
        pass

    async def get_client_id(self, force_refresh: bool = False) -> str:
        """Динамическое извлечение и кэширование актуального client_id из JS бандлов SoundCloud."""
        async with self._lock:
            if self._client_id and not force_refresh:
                return self._client_id

            logger.info("Поиск актуального SoundCloud client_id...")
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(headers=self.HEADERS, connector=connector) as session:
                try:
                    async with session.get("https://soundcloud.com", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
                            cdn_scripts = [s for s in scripts if "sndcdn.com/assets" in s]

                            for s_url in reversed(cdn_scripts):
                                try:
                                    async with session.get(s_url, timeout=aiohttp.ClientTimeout(total=8)) as s_resp:
                                        if s_resp.status == 200:
                                            js = await s_resp.text()
                                            matches = re.findall(r'client_id[:=]\s*"([0-9a-zA-Z]{32})"', js)
                                            if matches:
                                                self._client_id = matches[0]
                                                logger.info(f"Успешно найден client_id: {self._client_id}")
                                                return self._client_id
                                except Exception as e:
                                    logger.debug(f"Ошибка проверки скрипта {s_url}: {e}")
                                    continue
                except Exception as e:
                    logger.warning(f"Ошибка при получении HTML soundcloud.com: {e}")

            if not self._client_id:
                # Резервный проверенный публичный client_id
                fallback_id = "glXhJxvYKPM87la70dG2G1bJ03AdA3hN"
                logger.info(f"Используем запасной client_id: {fallback_id}")
                self._client_id = fallback_id

            return self._client_id

    def _format_track(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Преобразование сырых данных трека в нормализованную структуру."""
        artwork_url = item.get("artwork_url") or (item.get("user") or {}).get("avatar_url")
        if artwork_url and "-large." in artwork_url:
            artwork_url = artwork_url.replace("-large.", "-t500x500.")

        duration_sec = int((item.get("duration") or 0) / 1000)
        user = item.get("user") or {}
        artist = user.get("username", "Unknown Artist")
        title = item.get("title", "Untitled")

        # Проверяем, не является ли трек платным Go+ превью
        is_snipped = item.get("snipped", False) or item.get("policy") == "SNIP"
        # Проверяем наличие медиа потоков
        media = item.get("media", {})
        transcodings = media.get("transcodings", [])
        has_full_stream = False
        for t in transcodings:
            if not t.get("snipped") and "/preview/" not in t.get("url", ""):
                has_full_stream = True
                break

        return {
            "id": str(item.get("id")),
            "title": title,
            "artist": artist,
            "duration": duration_sec,
            "permalink_url": item.get("permalink_url", ""),
            "artwork_url": artwork_url,
            "genre": item.get("genre", ""),
            "likes_count": item.get("likes_count", 0),
            "playback_count": item.get("playback_count", 0),
            "is_preview": is_snipped or (not has_full_stream and len(transcodings) > 0),
            "policy": item.get("policy", "ALLOW")
        }

    async def get_track_by_id(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Получение метаданных трека по его ID."""
        client_id = await self.get_client_id()

        for attempt in range(2):
            try:
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(headers=self.HEADERS, connector=connector) as session:
                    params = {"client_id": client_id}
                    async with session.get(
                        f"{self.BASE_URL}/tracks/{track_id}",
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status in (401, 403):
                            client_id = await self.get_client_id(force_refresh=True)
                            continue
                        if resp.status == 404:
                            return None
                        if resp.status == 200:
                            data = await resp.json()
                            return self._format_track(data)
            except Exception as e:
                logger.error(f"Ошибка при получении трека {track_id}: {e}")
                if attempt == 0:
                    client_id = await self.get_client_id(force_refresh=True)

        return None

    async def search_tracks(self, query: str, limit: int = 15) -> List[Dict[str, Any]]:
        """Быстрый поиск треков по названию."""
        query = query.strip()
        if not query:
            return []

        client_id = await self.get_client_id()

        for attempt in range(2):
            try:
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(headers=self.HEADERS, connector=connector) as session:
                    params = {
                        "q": query,
                        "client_id": client_id,
                        "limit": limit,
                        "offset": 0
                    }
                    async with session.get(
                        f"{self.BASE_URL}/search/tracks",
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status in (401, 403):
                            logger.warning(f"SoundCloud API {resp.status}, обновляем client_id...")
                            client_id = await self.get_client_id(force_refresh=True)
                            continue

                        if resp.status == 200:
                            data = await resp.json()
                            results = []
                            for item in data.get("collection", []):
                                if item.get("kind") == "track":
                                    results.append(self._format_track(item))
                            return results
            except Exception as e:
                logger.error(f"Ошибка при поиске '{query}' (попытка {attempt+1}): {e}")
                if attempt == 0:
                    client_id = await self.get_client_id(force_refresh=True)

        return []

    async def resolve_url(self, url: str) -> Optional[Dict[str, Any]]:
        """Получение метаданных по ссылке на трек."""
        client_id = await self.get_client_id()

        for attempt in range(2):
            try:
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(headers=self.HEADERS, connector=connector) as session:
                    params = {
                        "url": url,
                        "client_id": client_id
                    }
                    async with session.get(
                        f"{self.BASE_URL}/resolve",
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status in (401, 403):
                            client_id = await self.get_client_id(force_refresh=True)
                            continue
                        if resp.status == 404:
                            return None
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("kind") == "track":
                                return self._format_track(data)
                            elif data.get("kind") == "playlist":
                                tracks = [self._format_track(t) for t in data.get("tracks", []) if t.get("kind") == "track"]
                                return {
                                    "kind": "playlist",
                                    "id": str(data.get("id")),
                                    "title": data.get("title", "SoundCloud Playlist"),
                                    "tracks": tracks
                                }
            except Exception as e:
                logger.error(f"Ошибка при резолве URL {url}: {e}")
                if attempt == 0:
                    client_id = await self.get_client_id(force_refresh=True)

        return None
