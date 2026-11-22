import os
import asyncio
import logging
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from config import BOT_TOKEN, PROXY_URL, CUSTOM_API_SERVER
from database import init_db
from handlers import router, sc_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("sc_bot")


async def keep_alive_task(external_url: str):
    """Фоновая задача самопинга для предотвращения засыпания бесплатного тарифа Render."""
    logger.info(f"Запущен keep-alive пинг для: {external_url}")
    await asyncio.sleep(60)  # Даем сервису сначала полностью подняться
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(external_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    logger.debug(f"Keep-alive пинг {external_url} -> статус {resp.status}")
        except Exception as e:
            logger.debug(f"Keep-alive ошибка пинга: {e}")
        # Пинг каждые 10 минут (Render засыпает через 15 минут неактивности)
        await asyncio.sleep(600)


async def main():
    logger.info("Инициализация базы данных...")
    await init_db()

    logger.info("Запуск Telegram бота...")

    session = None
    if CUSTOM_API_SERVER:
        from aiogram.client.session.aiohttp import AiohttpSession
        from aiogram.client.telegram import TelegramAPIServer

        logger.info(f"Используется Cloudflare Worker / API шлюз: {CUSTOM_API_SERVER}")
        api = TelegramAPIServer.from_base(CUSTOM_API_SERVER)
        session = AiohttpSession(api=api)
    elif PROXY_URL:
        from aiogram.client.session.aiohttp import AiohttpSession
        logger.info(f"Используется прокси для Telegram API: {PROXY_URL}")
        session = AiohttpSession(proxy=PROXY_URL)

    bot = Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()
    dp.include_router(router)

    # Устанавливаем команды бота
    commands = [
        BotCommand(command="start", description="Запустить бота / главное меню"),
        BotCommand(command="help", description="Инструкция по использованию"),
    ]
    try:
        await bot.set_my_commands(commands)
    except Exception as e:
        logger.warning(f"Не удалось установить команды бота: {e}")

    # Запуск легковесного HTTP-сервера для healthcheck (Render, Koyeb, etc.)
    runner = None
    port = os.getenv("PORT")
    if port:
        try:
            from aiohttp import web

            async def handle_ping(request):
                return web.Response(text="Bot is running!")

            app = web.Application()
            app.router.add_get("/", handle_ping)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", int(port))
            await site.start()
            logger.info(f"Healthcheck HTTP сервер успешно запущен на порту {port}")
        except Exception as e:
            logger.warning(f"Не удалось запустить healthcheck сервер: {e}")

    # Если бот развернут на Render, запускаем фоновый keep-alive
    external_url = os.getenv("RENDER_EXTERNAL_URL")
    keep_alive_handle = None
    if external_url:
        keep_alive_handle = asyncio.create_task(keep_alive_task(external_url))

    # Удаляем вебхук и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот успешно запущен и готов к работе!")

    try:
        await dp.start_polling(bot)
    finally:
        logger.info("Завершение работы бота и освобождение ресурсов...")
        if keep_alive_handle:
            keep_alive_handle.cancel()
        if runner:
            await runner.cleanup()
        await sc_api.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
