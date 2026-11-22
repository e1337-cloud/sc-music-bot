import asyncio
import os
import uuid
import logging
from pathlib import Path
from typing import Dict, Any, Optional
import yt_dlp
import aiohttp
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, error as ID3Error

from config import DOWNLOADS_DIR, FFMPEG_PATH

logger = logging.getLogger(__name__)

# Семафор для ограничения одновременных загрузок (максимум 2 параллельно для экономии RAM/CPU)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

# Лимит размера файла в Telegram Bot API (50 МБ)
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024

class DownloadError(Exception):
    pass

class DrmProtectedError(DownloadError):
    pass

class FileSizeLimitError(DownloadError):
    pass


def _sync_download(url: str, output_id: str) -> Dict[str, Any]:
    """Синхронная функция скачивания через yt-dlp."""
    out_template = str(DOWNLOADS_DIR / f"{output_id}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': out_template,
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'socket_timeout': 15,
        'retries': 3,
    }

    if FFMPEG_PATH and os.path.exists(FFMPEG_PATH):
        ydl_opts['ffmpeg_location'] = FFMPEG_PATH
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise DownloadError("Не удалось получить информацию о треке.")

            title = (info.get('title') or "Untitled").strip()
            uploader = (info.get('uploader') or "SoundCloud Artist").strip()
            duration = int(info.get('duration') or 0)
            thumbnail_url = info.get('thumbnail')

            # Ищем созданный файл
            mp3_path = DOWNLOADS_DIR / f"{output_id}.mp3"
            if mp3_path.exists():
                final_file = mp3_path
            else:
                # Если ffmpeg не сконвертировал, ищем с другим расширением (m4a, opus, aac)
                found = list(DOWNLOADS_DIR.glob(f"{output_id}.*"))
                if not found:
                    raise DownloadError("Файл не был сохранен на диск.")
                final_file = found[0]

            # Проверяем размер файла на лимит Telegram (50 МБ)
            file_size = final_file.stat().st_size
            if file_size > MAX_FILE_SIZE_BYTES:
                size_mb = file_size / (1024 * 1024)
                try:
                    final_file.unlink(missing_ok=True)
                except Exception:
                    pass
                raise FileSizeLimitError(
                    f"Размер трека ({size_mb:.1f} МБ) превышает ограничение Telegram в 50 МБ."
                )

            return {
                "file_path": str(final_file),
                "title": title,
                "artist": uploader,
                "duration": duration,
                "thumbnail_url": thumbnail_url,
                "id": str(info.get('id', output_id))
            }

    except yt_dlp.utils.ExtractorError as e:
        err_msg = str(e).lower()
        if "drm" in err_msg or "protected" in err_msg:
            raise DrmProtectedError("Трек защищен DRM (SoundCloud Go+) и недоступен для полного скачивания.")
        raise DownloadError(f"Ошибка извлечения: {e}")
    except (DrmProtectedError, FileSizeLimitError):
        raise
    except Exception as e:
        err_msg = str(e).lower()
        if "drm" in err_msg or "protected" in err_msg:
            raise DrmProtectedError("Трек защищен DRM (SoundCloud Go+) и недоступен для полного скачивания.")
        raise DownloadError(f"Ошибка загрузки: {e}")


async def download_track(url: str, artwork_url: Optional[str] = None) -> Dict[str, Any]:
    """Асинхронная обертка для скачивания трека и вшивания тегов с контролем параллелизма."""
    output_id = str(uuid.uuid4())[:8]

    async with DOWNLOAD_SEMAPHORE:
        # Запускаем тяжелый процесс yt-dlp в отдельном пуле потоков
        track_info = await asyncio.to_thread(_sync_download, url, output_id)
        file_path = Path(track_info["file_path"])

        # Скачиваем обложку для Telegram и ID3-тегов
        thumb_path = None
        target_thumb_url = artwork_url or track_info.get("thumbnail_url")
        if target_thumb_url:
            thumb_path = DOWNLOADS_DIR / f"{output_id}_thumb.jpg"
            try:
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.get(target_thumb_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        if resp.status == 200:
                            thumb_bytes = await resp.read()
                            with open(thumb_path, "wb") as f:
                                f.write(thumb_bytes)
            except Exception as e:
                logger.warning(f"Не удалось скачать обложку: {e}")
                thumb_path = None

        # Вшиваем теги ID3 (название, автор, обложка) если это MP3
        if file_path.suffix.lower() == ".mp3":
            try:
                audio = MP3(file_path, ID3=ID3)
                try:
                    audio.add_tags()
                except ID3Error:
                    pass

                # Обрезаем названия до 250 символов для стандарта ID3
                safe_title = track_info["title"][:250].replace('\x00', '')
                safe_artist = track_info["artist"][:250].replace('\x00', '')

                audio.tags.add(TIT2(encoding=3, text=safe_title))
                audio.tags.add(TPE1(encoding=3, text=safe_artist))

                if thumb_path and thumb_path.exists():
                    with open(thumb_path, "rb") as alb:
                        audio.tags.add(APIC(
                            encoding=3,
                            mime="image/jpeg",
                            type=3,
                            desc="Cover",
                            data=alb.read()
                        ))
                audio.save()
            except Exception as e:
                logger.warning(f"Ошибка добавления ID3 тегов: {e}")

        track_info["thumb_path"] = str(thumb_path) if thumb_path and thumb_path.exists() else None
        return track_info


def cleanup_files(*file_paths: Optional[str]):
    """Гарантированное удаление временных файлов."""
    for path_str in file_paths:
        if path_str:
            try:
                p = Path(path_str)
                if p.exists():
                    p.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"Не удалось удалить временный файл {path_str}: {e}")
