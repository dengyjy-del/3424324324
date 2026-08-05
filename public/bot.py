"""
LOOKSMAX ANALYSIS BOT — точка входа.

Запуск:
    pip install -r requirements.txt
    cp .env.example .env   # и вписать BOT_TOKEN от @BotFather
    python bot.py
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

import handlers
import texts
from access import DemoState, SubscriptionGate
from config import Config, load_config
from database import create_database
from middlewares import SubscriptionMiddleware, ThrottleMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("looksmax")

COMMANDS = [
    BotCommand(command="start", description="🧬 Главный экран"),
    BotCommand(command="stats", description="📊 Моя статистика"),
    BotCommand(command="about", description="ℹ️ О боте"),
    BotCommand(command="help", description="📖 Справка"),
]


def _log_setup(config: Config) -> None:
    if config.gate_enabled:
        logger.info("Гейт подписки: %s", config.channel_id)
        logger.info("Бот должен быть администратором канала, иначе проверка не работает")
    else:
        logger.info("Гейт подписки выключен (CHANNEL_ID пуст)")

    if not config.demo_code:
        logger.info("Режим съёмки выключен (DEMO_CODE пуст)")
    elif config.admin_ids:
        logger.info("Режим съёмки: доступен ID %s", ", ".join(map(str, config.admin_ids)))
    else:
        logger.warning(
            "ADMIN_IDS пуст — режим съёмки сработает у любого, кто узнает код. "
            "Отправь боту /myid и впиши свой ID в .env"
        )


async def run(config: Config) -> None:
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    texts.configure(config.brand_name)

    db = create_database(config.database_url)
    await db.connect()

    dispatcher = Dispatcher()
    dispatcher["db"] = db
    dispatcher["config"] = config
    gate = SubscriptionGate(config.channel_id)
    dispatcher["gate"] = gate
    demo_state = DemoState(db, config.demo_ttl_minutes)
    dispatcher["demo"] = demo_state

    throttle = ThrottleMiddleware(config.cooldown_seconds)
    subscription = SubscriptionMiddleware()
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.middleware(throttle)
        observer.middleware(subscription)

    dispatcher.include_router(handlers.router)

    tasks = []
    if config.webapp_enabled:
        tasks.append(asyncio.create_task(_serve_webapp(config, db, demo_state, gate, bot)))
        logger.info("Мини-апп: %s (порт %s)", config.webapp_url, config.webapp_port)
    else:
        logger.info("Мини-апп выключен (WEBAPP_URL пуст)")

    try:
        await bot.set_my_commands(COMMANDS)
        me = await bot.get_me()
        logger.info("Бот запущен: @%s", me.username)
        _log_setup(config)
        if config.webapp_enabled:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Приложение", web_app=WebAppInfo(url=config.webapp_url)
                )
            )
        # Локальный режим работает на polling, а он несовместим с вебхуком:
        # Telegram разрешает что-то одно. Поэтому вебхук приходится снять —
        # но это выключает бота, поднятого на Vercel, поэтому предупреждаем.
        hook = await bot.get_webhook_info()
        if hook.url:
            logger.warning("=" * 70)
            logger.warning("Снимаю вебхук %s", hook.url)
            logger.warning(
                "Бот на хостинге перестанет отвечать, пока работает эта копия."
            )
            logger.warning(
                "Чтобы вернуть его: останови этот процесс и открой "
                "<адрес приложения>/api/setup?key=<SETUP_KEY>"
            )
            logger.warning("=" * 70)

        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()
        await db.close()
        await bot.session.close()
        logger.info("Бот остановлен")


async def _serve_webapp(config: Config, db, demo: DemoState, gate, bot) -> None:
    """Мини-апп живёт в том же процессе и на том же event loop, что и бот."""
    import uvicorn

    from webapp.server import create_app

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config, db, demo, gate, bot),
            host=config.webapp_host,
            port=config.webapp_port,
            log_level="warning",
            access_log=False,
        )
    )
    await server.serve()


def main() -> None:
    try:
        config = load_config()
    except RuntimeError as error:
        print(f"\n[!] {error}\n", file=sys.stderr)
        raise SystemExit(1) from error

    try:
        asyncio.run(run(config))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Выход по сигналу пользователя")


if __name__ == "__main__":
    main()
