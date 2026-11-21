import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from config import BOT_TOKEN, PROXY_URL, CUSTOM_API_SERVER
from database import init_db
from handlers import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("sc_bot")

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
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="help", description="Инструкция по использованию"),
    ]
    try:
        await bot.set_my_commands(commands)
    except Exception as e:
        logger.warning(f"Не удалось установить команды бота: {e}")

    # Запуск легковесного HTTP-сервера для healthcheck (для Render, Koyeb, Hugging Face и т.д.)
    import os
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
            logger.info(f"Healthcheck HTTP сервер запущен на порту {port}")
        except Exception as e:
            logger.warning(f"Не удалось запустить healthcheck сервер: {e}")

    # Удаляем вебхук и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот успешно запущен и готов к работе!")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
