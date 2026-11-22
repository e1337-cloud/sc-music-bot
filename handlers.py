import html
import logging
import re
from typing import Optional
from aiogram import Router, F, Bot
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
from aiogram.filters import CommandStart, Command, CommandObject

from sc_api import SoundCloudAPI
from downloader import (
    download_track,
    cleanup_files,
    DrmProtectedError,
    FileSizeLimitError,
    DownloadError
)
from database import get_cached_track_by_id, get_cached_track_by_url, save_track_cache

logger = logging.getLogger(__name__)
router = Router()
sc_api = SoundCloudAPI()

def format_duration(seconds: Optional[int]) -> str:
    """Форматирование длительности в mm:ss или hh:mm:ss."""
    sec = int(seconds or 0)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

SC_URL_REGEX = re.compile(r'https?://(m.|www.|on.)?soundcloud.com/[^\s]+')


# ==========================================
# 1. КОМАНДЫ БОТА (/start, /help)
# ==========================================

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, bot: Bot):
    # Проверяем deep-link аргумент (например, /start dl_123456789)
    args = command.args
    if args and args.startswith("dl_"):
        track_id = args.split("dl_", 1)[1]
        await process_download_and_send(message, track_id, bot)
        return

    bot_info = await bot.get_me()
    bot_username = bot_info.username or "bot"

    text = (
        "👋 <b>Привет! Я бот для поиска и скачивания музыки с SoundCloud.</b>\n\n"
        "✨ <b>Возможности:</b>\n"
        f"• <b>Инлайн-поиск</b>: напиши <code>@{bot_username} название трека</code> в любом чате и выбери песню.\n"
        "• <b>Скачивание по ссылке</b>: просто отправь мне ссылку на трек из SoundCloud.\n"
        "• <b>Поиск в чате</b>: отправь название трека или автора сообщением.\n\n"
        "⚡ <i>Треки кэшируются и при повторных запросах отправляются мгновенно!</i>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Попробовать инлайн-поиск", switch_inline_query_current_chat="")],
        [InlineKeyboardButton(text="🌐 Открыть SoundCloud", url="https://soundcloud.com")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message, bot: Bot):
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "bot"

    text = (
        "📖 <b>Инструкция по использованию:</b>\n\n"
        f"1. <b>Инлайн-поиск:</b>\n"
        f"В любом диалоге введи имя бота через @ и название трека, например:\n"
        f"<code>@{bot_username} The Weeknd</code>\n\n"
        "2. <b>Скачивание по ссылке:</b>\n"
        "Скопируй ссылку из приложения SoundCloud или браузера и отправь её боту.\n"
        "Поддерживаются как обычные ссылки, так и короткие <code>on.soundcloud.com</code>.\n\n"
        "3. <b>Ограничения:</b>\n"
        "• Треки с платной подпиской SoundCloud Go+ защищены DRM и недоступны для полного скачивания.\n"
        "• Ограничение Telegram на размер аудиофайла — до 50 МБ."
    )
    await message.answer(text, parse_mode="HTML")


# ==========================================
# 2. ИНЛАЙН ПОИСК (INLINE MODE)
# ==========================================

@router.inline_query()
async def inline_search(inline_query: InlineQuery, bot: Bot):
    query = inline_query.query.strip()
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "bot"

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

            safe_title = html.escape(title)
            safe_artist = html.escape(artist)
            safe_url = html.escape(permalink_url, quote=True)

            # Проверяем, есть ли трек в кэше бота (уже загружен в Telegram)
            cached = await get_cached_track_by_id(track_id)
            if cached and cached.get("telegram_file_id"):
                results.append(
                    InlineQueryResultCachedAudio(
                        id=f"cached_{track_id}",
                        audio_file_id=cached["telegram_file_id"],
                        caption=f"🎵 <a href='{safe_url}'>{safe_title}</a> — {safe_artist}",
                        parse_mode="HTML"
                    )
                )
            else:
                caption_text = (
                    f"🎶 <b>{safe_title}</b>\n"
                    f"👤 Исполнитель: <i>{safe_artist}</i>\n"
                    f"⏱ Длительность: {dur_str}\n\n"
                    f"🔗 <a href='{safe_url}'>Открыть в SoundCloud</a>"
                )

                # В инлайн-режиме кнопка скачивания ведет прямо в бота через deep link
                download_url = f"https://t.me/{bot_username}?start=dl_{track_id}"
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📥 Скачать MP3 в боте", url=download_url)],
                    [InlineKeyboardButton(text="🎧 Слушать в SC", url=permalink_url)]
                ])

                results.append(
                    InlineQueryResultArticle(
                        id=f"art_{track_id}",
                        title=title,
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
async def on_download_click(callback: CallbackQuery, bot: Bot):
    track_id = callback.data.split(":", 1)[1]

    # Если callback пришел из инлайн-сообщения (в группе/канале), callback.message равен None
    if callback.message is None:
        bot_info = await bot.get_me()
        bot_username = bot_info.username or "bot"
        await callback.answer(
            url=f"https://t.me/{bot_username}?start=dl_{track_id}"
        )
        return

    await callback.answer("⏳ Проверяю трек...", show_alert=False)
    await process_download_and_send(callback.message, track_id, bot)


async def process_download_and_send(target_message: Message, track_id: str, bot: Bot):
    """Общая функция скачивания и отправки трека пользователю."""
    # 1. Проверяем кэш
    cached = await get_cached_track_by_id(track_id)
    if cached and cached.get("telegram_file_id"):
        try:
            safe_title = html.escape(cached.get("title") or "Track")
            safe_url = html.escape(cached.get("permalink_url") or "", quote=True)
            caption = f"🎵 <a href='{safe_url}'>{safe_title}</a>" if safe_url else None

            await target_message.answer_audio(
                audio=cached["telegram_file_id"],
                title=cached.get("title"),
                performer=cached.get("artist"),
                duration=cached.get("duration"),
                caption=caption,
                parse_mode="HTML"
            )
            return
        except Exception as e:
            logger.warning(f"Не удалось отправить трек из кэша (возможно, устарел file_id): {e}")

    # 2. Получаем метаданные трека
    track_info = await sc_api.get_track_by_id(track_id)
    if not track_info:
        await target_message.answer("❌ Не удалось найти информацию о треке на SoundCloud.")
        return

    permalink_url = track_info.get("permalink_url")
    if not permalink_url:
        await target_message.answer("❌ Ссылка на трек недоступна.")
        return

    safe_title = html.escape(track_info['title'])
    status_msg = await target_message.answer(
        f"⏳ Скачиваю <b>{safe_title}</b>...",
        parse_mode="HTML"
    )

    file_path = None
    thumb_path = None

    try:
        downloaded = await download_track(permalink_url, artwork_url=track_info.get("artwork_url"))
        file_path = downloaded["file_path"]
        thumb_path = downloaded.get("thumb_path")

        audio_input = FSInputFile(file_path)
        thumb_input = FSInputFile(thumb_path) if thumb_path else None
        safe_url = html.escape(permalink_url, quote=True)

        sent_msg = await target_message.answer_audio(
            audio=audio_input,
            title=track_info["title"],
            performer=track_info["artist"],
            duration=track_info["duration"],
            thumbnail=thumb_input,
            caption=f"🎵 <a href='{safe_url}'>{safe_title}</a>",
            parse_mode="HTML"
        )

        if sent_msg.audio:
            await save_track_cache(
                track_id=track_id,
                permalink_url=permalink_url,
                telegram_file_id=sent_msg.audio.file_id,
                title=track_info["title"],
                artist=track_info["artist"],
                duration=track_info["duration"]
            )

        try:
            await status_msg.delete()
        except Exception:
            pass

    except DrmProtectedError:
        await status_msg.edit_text(
            "🔒 <b>Трек недоступен для скачивания</b>\n"
            "Этот трек защищен SoundCloud Go+ DRM и доступен только по платной подписке.",
            parse_mode="HTML"
        )
    except FileSizeLimitError as e:
        await status_msg.edit_text(
            f"⚠️ <b>Файл слишком большой:</b> {html.escape(str(e))}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка загрузки трека {track_id}: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка при скачивании трека:</b> {html.escape(str(e))}",
            parse_mode="HTML"
        )
    finally:
        # Гарантированная очистка временных файлов диска при любых ошибках
        cleanup_files(file_path, thumb_path)


# ==========================================
# 4. СООБЩЕНИЯ В ЧАТЕ (ССЫЛКИ И ПОИСК)
# ==========================================

@router.message(F.text)
async def on_user_message(message: Message, bot: Bot):
    text = (message.text or "").strip()
    if not text:
        return

    # Проверяем, есть ли в сообщении ссылка на SoundCloud
    match = SC_URL_REGEX.search(text)
    if match:
        url = match.group(0)
        await handle_sc_url(message, url, bot)
        return

    # Если ссылка не найдена, обрабатываем как текстовый поисковый запрос
    await handle_text_search(message, text)


async def handle_sc_url(message: Message, url: str, bot: Bot):
    """Обработка прямой ссылки на трек или плейлист SoundCloud."""
    status_msg = await message.answer("🔍 Распознаю ссылку SoundCloud...")

    # Проверяем кэш базы данных
    cached = await get_cached_track_by_url(url)
    if cached and cached.get("telegram_file_id"):
        try:
            await status_msg.delete()
        except Exception:
            pass
        safe_title = html.escape(cached.get("title") or "Track")
        safe_url = html.escape(cached.get("permalink_url") or "", quote=True)
        caption = f"🎵 <a href='{safe_url}'>{safe_title}</a>" if safe_url else None

        await message.answer_audio(
            audio=cached["telegram_file_id"],
            title=cached.get("title"),
            performer=cached.get("artist"),
            duration=cached.get("duration"),
            caption=caption,
            parse_mode="HTML"
        )
        return

    # Резолвим ссылку через API
    resolved = await sc_api.resolve_url(url)
    if not resolved:
        await status_msg.edit_text("❌ Не удалось найти трек по этой ссылке. Возможно, он скрыт или удален.")
        return

    if resolved.get("kind") == "playlist":
        playlist_title = resolved.get("title", "Плейлист")
        tracks = resolved.get("tracks", [])[:10]  # Ограничиваем первыми 10 треками
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
    safe_title = html.escape(title)

    await status_msg.edit_text(f"⏳ Скачиваю <b>{safe_title}</b>...", parse_mode="HTML")

    file_path = None
    thumb_path = None

    try:
        downloaded = await download_track(permalink_url, artwork_url=track.get("artwork_url"))
        file_path = downloaded["file_path"]
        thumb_path = downloaded.get("thumb_path")

        audio_input = FSInputFile(file_path)
        thumb_input = FSInputFile(thumb_path) if thumb_path else None
        safe_url = html.escape(permalink_url, quote=True)

        sent_msg = await message.answer_audio(
            audio=audio_input,
            title=title,
            performer=artist,
            duration=track["duration"],
            thumbnail=thumb_input,
            caption=f"🎵 <a href='{safe_url}'>{safe_title}</a>",
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

        try:
            await status_msg.delete()
        except Exception:
            pass

    except DrmProtectedError:
        await status_msg.edit_text(
            "🔒 <b>Трек защищен авторскими правами (SoundCloud Go+ DRM)</b> и недоступен для загрузки.",
            parse_mode="HTML"
        )
    except FileSizeLimitError as e:
        await status_msg.edit_text(
            f"⚠️ <b>Файл слишком большой:</b> {html.escape(str(e))}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка при скачивании по URL {url}: {e}")
        await status_msg.edit_text(f"❌ Ошибка скачивания: {html.escape(str(e))}")
    finally:
        cleanup_files(file_path, thumb_path)


async def handle_text_search(message: Message, query: str):
    """Поиск по названию в личных сообщениях."""
    if len(query) < 1:
        return

    safe_query = html.escape(query)
    status_msg = await message.answer(f"🔎 Ищу <b>{safe_query}</b> на SoundCloud...", parse_mode="HTML")

    tracks = await sc_api.search_tracks(query, limit=5)
    if not tracks:
        await status_msg.edit_text("😔 Ничего не найдено. Попробуй изменить запрос.")
        return

    buttons = []
    text_lines = [f"🎶 <b>Результаты поиска по запросу «{safe_query}»:</b>\n"]

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
