import html
import logging
import re
from typing import Optional
from aiogram import Router, F
from aiogram.types import (
    Message,
    InlineQuery,
    CallbackQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedAudio,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile
)
from aiogram.filters import CommandStart, Command

from sc_api import SoundCloudAPI
from downloader import download_track, cleanup_files, DrmProtectedError, DownloadError
from database import get_cached_track_by_id, get_cached_track_by_url, save_track_cache

logger = logging.getLogger(__name__)
router = Router()
sc_api = SoundCloudAPI()

def format_duration(seconds: int) -> str:
    """Форматирование длительности в mm:ss или hh:mm:ss."""
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

SC_URL_REGEX = re.compile(r'https?://(m\.|www\.|on\.)?soundcloud\.com/[^\s]+')


# ==========================================
# 1. КОМАНДЫ БОТА (/start, /help)
# ==========================================

@router.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "👋 <b>Привет! Я бот для поиска и скачивания музыки с SoundCloud.</b>\n\n"
        "✨ <b>Возможности:</b>\n"
        "• <b>Инлайн-поиск</b>: просто напиши <code>@бот название трека</code> в любом чате и выбери песню.\n"
        "• <b>Скачивание по ссылке</b>: отправь мне ссылку на трек из SoundCloud.\n"
        "• <b>Поиск в чате</b>: отправь название трека или имя исполнителя сообщением.\n\n"
        "⚡ <i>Треки сохраняются в кэше и при повторном запросе отправляются мгновенно!</i>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Попробовать инлайн-поиск", switch_inline_query_current_chat="")],
        [InlineKeyboardButton(text="🌐 Открыть SoundCloud", url="https://soundcloud.com")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📖 <b>Инструкция по использованию:</b>\n\n"
        "1. <b>Инлайн-поиск:</b>\n"
        "В любом диалоге введи имя бота через @ и название трека, например:\n"
        "<code>@{bot_username} The Weeknd</code>\n\n"
        "2. <b>Скачивание по ссылке:</b>\n"
        "Скопируй ссылку из приложения SoundCloud или браузера и отправь её боту.\n\n"
        "3. <b>Ограничения:</b>\n"
        "• Треки с платной подпиской SoundCloud Go+ защищены DRM и недоступны для полного скачивания.\n"
        "• Ограничение Telegram на размер аудиофайла — до 50 МБ."
    )
    bot_info = await message.bot.get_me()
    text = text.format(bot_username=bot_info.username or "bot")
    await message.answer(text, parse_mode="HTML")


# ==========================================
# 2. ИНЛАЙН ПОИСК (INLINE MODE)
# ==========================================

@router.inline_query()
async def inline_search(inline_query: InlineQuery):
    query = inline_query.query.strip()

    # Если запрос пустой, показываем подсказку
    if not query:
        placeholder = InlineQueryResultArticle(
            id="hint",
            title="🔍 Введи название трека или исполнителя",
            description="Например: Travis Scott, Phonk, Remix...",
            input_message_content=InputTextMessageContent(
                message_text="🎧 <i>Используй инлайн-поиск бота, чтобы делиться треками в чатах!</i>",
                parse_mode="HTML"
            )
        )
        await inline_query.answer([placeholder], cache_time=5, is_personal=True)
        return

    try:
        tracks = await sc_api.search_tracks(query, limit=20)
        results = []

        for track in tracks:
            track_id = track["id"]
            title = track["title"]
            artist = track["artist"]
            duration = track["duration"]
            artwork_url = track["artwork_url"]
            permalink_url = track["permalink_url"]
            dur_str = format_duration(duration)

            # Проверяем, есть ли трек в кэше бота (уже загружен в Telegram)
            cached = await get_cached_track_by_id(track_id)
            if cached and cached.get("telegram_file_id"):
                # Если уже загружен — отдаем аудио напрямую мгновенно!
                results.append(
                    InlineQueryResultCachedAudio(
                        id=f"cached_{track_id}",
                        audio_file_id=cached["telegram_file_id"],
                        caption=f"🎵 <a href='{permalink_url}'>{html.escape(title)}</a> — {html.escape(artist)}",
                        parse_mode="HTML"
                    )
                )
            else:
                # Если еще не загружен — отправляем карточку с кнопкой на скачивание
                caption_text = (
                    f"🎶 <b>{html.escape(title)}</b>\n"
                    f"👤 Исполнитель: <i>{html.escape(artist)}</i>\n"
                    f"⏱ Длительность: {dur_str}\n\n"
                    f"🔗 <a href='{permalink_url}'>Открыть в SoundCloud</a>"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📥 Скачать MP3", callback_data=f"dl:{track_id}")],
                    [InlineKeyboardButton(text="🎧 Слушать в SC", url=permalink_url)]
                ])

                results.append(
                    InlineQueryResultArticle(
                        id=f"art_{track_id}",
                        title=f"{title}",
                        description=f"{artist} • {dur_str}",
                        thumbnail_url=artwork_url,
                        input_message_content=InputTextMessageContent(
                            message_text=caption_text,
                            parse_mode="HTML",
                            disable_web_page_preview=False
                        ),
                        reply_markup=kb
                    )
                )

        await inline_query.answer(results, cache_time=30, is_personal=False)

    except Exception as e:
        logger.error(f"Ошибка при обработке inline_query '{query}': {e}")
        await inline_query.answer([], cache_time=5)


# ==========================================
# 3. ОБРАБОТКА НАЖАТИЯ КНОПКИ СКАЧИВАНИЯ
# ==========================================

@router.callback_query(F.data.startswith("dl:"))
async def on_download_click(callback: CallbackQuery):
    track_id = callback.data.split(":", 1)[1]

    # Отвечаем на callback, чтобы у пользователя не крутился индикатор
    await callback.answer("⏳ Проверяю трек...", show_alert=False)

    # 1. Проверяем кэш
    cached = await get_cached_track_by_id(track_id)
    if cached and cached.get("telegram_file_id"):
        try:
            await callback.message.reply_audio(
                audio=cached["telegram_file_id"],
                title=cached.get("title"),
                performer=cached.get("artist"),
                duration=cached.get("duration")
            )
            return
        except Exception as e:
            logger.warning(f"Не удалось отправить трек из кэша: {e}")

    # 2. Получаем метаданные трека
    track_info = await sc_api.get_track_by_id(track_id)
    if not track_info:
        await callback.message.reply("❌ Не удалось найти информацию о треке на SoundCloud.")
        return

    permalink_url = track_info.get("permalink_url")
    if not permalink_url:
        await callback.message.reply("❌ Ссылка на трек недоступна.")
        return

    # Сообщение о статусе загрузки
    status_msg = await callback.message.reply(
        f"⏳ Скачиваю <b>{html.escape(track_info['title'])}</b>...",
        parse_mode="HTML"
    )

    try:
        # Скачиваем через yt-dlp
        downloaded = await download_track(permalink_url, artwork_url=track_info.get("artwork_url"))
        file_path = downloaded["file_path"]
        thumb_path = downloaded.get("thumb_path")

        audio_input = FSInputFile(file_path)
        thumb_input = FSInputFile(thumb_path) if thumb_path else None

        sent_msg = await callback.message.reply_audio(
            audio=audio_input,
            title=track_info["title"],
            performer=track_info["artist"],
            duration=track_info["duration"],
            thumbnail=thumb_input,
            caption=f"🎵 <a href='{permalink_url}'>{html.escape(track_info['title'])}</a>",
            parse_mode="HTML"
        )

        # Сохраняем в кэш Telegram file_id
        if sent_msg.audio:
            await save_track_cache(
                track_id=track_id,
                permalink_url=permalink_url,
                telegram_file_id=sent_msg.audio.file_id,
                title=track_info["title"],
                artist=track_info["artist"],
                duration=track_info["duration"]
            )

        # Удаляем сообщение о загрузке
        await status_msg.delete()

        # Очищаем временные файлы
        cleanup_files(file_path, thumb_path)

    except DrmProtectedError:
        await status_msg.edit_text(
            "🔒 <b>Трек недоступен для скачивания</b>\n"
            "Этот трек защищен SoundCloud Go+ DRM или доступен только по платной подписке.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка загрузки трека {track_id}: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка при скачивании трека:</b> {html.escape(str(e))}",
            parse_mode="HTML"
        )


# ==========================================
# 4. СООБЩЕНИЯ В ЧАТЕ (ССЫЛКИ И ПОИСК)
# ==========================================

@router.message(F.text)
async def on_user_message(message: Message):
    text = message.text.strip()

    # Проверяем, есть ли в сообщении ссылка на SoundCloud
    match = SC_URL_REGEX.search(text)
    if match:
        url = match.group(0)
        await handle_sc_url(message, url)
        return

    # Если ссылка не найдена, обрабатываем как текстовый поисковый запрос
    await handle_text_search(message, text)


async def handle_sc_url(message: Message, url: str):
    """Обработка прямой ссылки на трек или плейлист SoundCloud."""
    status_msg = await message.answer("🔍 Распознаю ссылку SoundCloud...")

    # Проверяем кэш базы данных
    cached = await get_cached_track_by_url(url)
    if cached and cached.get("telegram_file_id"):
        await status_msg.delete()
        await message.answer_audio(
            audio=cached["telegram_file_id"],
            title=cached.get("title"),
            performer=cached.get("artist"),
            duration=cached.get("duration")
        )
        return

    # Резолвим ссылку через API
    resolved = await sc_api.resolve_url(url)
    if not resolved:
        await status_msg.edit_text("❌ Не удалось найти трек по этой ссылке. Возможно, он скрыт или удален.")
        return

    if resolved.get("kind") == "playlist":
        playlist_title = resolved.get("title", "Плейлист")
        tracks = resolved.get("tracks", [])[:10]  # Ограничиваем первыми 10
        if not tracks:
            await status_msg.edit_text("❌ В этом плейлисте нет доступных треков.")
            return

        kb = []
        for t in tracks:
            kb.append([InlineKeyboardButton(text=f"⬇️ {t['title'][:35]}", callback_data=f"dl:{t['id']}")])

        await status_msg.edit_text(
            f"📁 <b>Плейлист:</b> {html.escape(playlist_title)}\n"
            f"Найдено треков: {len(tracks)}\n"
            f"Выбери трек для загрузки:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
            parse_mode="HTML"
        )
        return

    # Это одиночный трек
    track = resolved
    track_id = track["id"]
    title = track["title"]
    artist = track["artist"]
    permalink_url = track["permalink_url"]

    await status_msg.edit_text(f"⏳ Скачиваю <b>{html.escape(title)}</b>...", parse_mode="HTML")

    try:
        downloaded = await download_track(permalink_url, artwork_url=track.get("artwork_url"))
        file_path = downloaded["file_path"]
        thumb_path = downloaded.get("thumb_path")

        audio_input = FSInputFile(file_path)
        thumb_input = FSInputFile(thumb_path) if thumb_path else None

        sent_msg = await message.answer_audio(
            audio=audio_input,
            title=title,
            performer=artist,
            duration=track["duration"],
            thumbnail=thumb_input,
            caption=f"🎵 <a href='{permalink_url}'>{html.escape(title)}</a>",
            parse_mode="HTML"
        )

        if sent_msg.audio:
            await save_track_cache(
                track_id=track_id,
                permalink_url=permalink_url,
                telegram_file_id=sent_msg.audio.file_id,
                title=title,
                artist=artist,
                duration=track["duration"]
            )

        await status_msg.delete()
        cleanup_files(file_path, thumb_path)

    except DrmProtectedError:
        await status_msg.edit_text(
            "🔒 <b>Трек защищен авторскими правами (SoundCloud Go+ DRM)</b> и недоступен для загрузки.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка при скачивании по URL {url}: {e}")
        await status_msg.edit_text(f"❌ Ошибка скачивания: {html.escape(str(e))}")


async def handle_text_search(message: Message, query: str):
    """Поиск по названию в личных сообщениях."""
    if len(query) < 2:
        return

    status_msg = await message.answer(f"🔎 Ищу <b>{html.escape(query)}</b> на SoundCloud...", parse_mode="HTML")

    tracks = await sc_api.search_tracks(query, limit=5)
    if not tracks:
        await status_msg.edit_text("😔 Ничего не найдено. Попробуй изменить запрос.")
        return

    buttons = []
    text_lines = [f"🎶 <b>Результаты поиска по запросу «{html.escape(query)}»:</b>\n"]

    for i, t in enumerate(tracks, 1):
        dur = format_duration(t['duration'])
        text_lines.append(f"{i}. <b>{html.escape(t['title'])}</b> — <i>{html.escape(t['artist'])}</i> ({dur})")
        buttons.append([
            InlineKeyboardButton(text=f"⬇️ {i}. {t['title'][:30]}", callback_data=f"dl:{t['id']}")
        ])

    buttons.append([
        InlineKeyboardButton(text="🔍 Искать в инлайне", switch_inline_query_current_chat=query)
    ])

    await status_msg.edit_text(
        "\n".join(text_lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
