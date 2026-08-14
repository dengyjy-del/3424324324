"""
LOOKSMAX ANALYSIS BOT — точка входа.

Запуск:
    pip install -r requirements.txt
    cp .env.example .env   # и вписать BOT_TOKEN от @BotFather
                           # зеркала — в BOT_TOKENS через запятую
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


def _warn_split_database(config: Config) -> None:
    """
    Локальный запуск с SQLite при живом мини-аппе означает две разные базы.

    Симптом коварный: и бот, и приложение работают, но видят разные данные —
    разметка уходит в одну базу, статистика читается из другой.
    """
    if config.uses_postgres or not config.webapp_url:
        return

    logger.warning("=" * 72)
    logger.warning("ВНИМАНИЕ: бот работает на локальной базе %s", config.database_url)
    logger.warning("А мини-апп по адресу %s — на своей.", config.webapp_url)
    logger.warning("Данные будут расходиться: разметка, XP, серии и рефералы")
    logger.warning("окажутся в разных местах.")
    logger.warning("")
    logger.warning("Чтобы база была общей, пропиши в локальный .env строку")
    logger.warning("подключения Postgres из Vercel:  DATABASE_URL=postgresql://...")
    logger.warning("=" * 72)


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
    # Один процесс тянет и основного бота, и зеркала: у aiogram один
    # диспетчер умеет опрашивать несколько ботов сразу.
    bots = [
        Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        for token in config.bot_tokens
    ]
    bot = bots[0]

    texts.configure(config.brand_name)

    db = create_database(config.database_url)
    await db.connect()

    # Таблицы раздела оценок живут в той же базе и на том же соединении.
    from mograte.core import db as rate_db
    from mograte.core import seed_loader as rate_seed

    await rate_db.attach(db)
    await rate_seed.load(verbose=True)

    dispatcher = Dispatcher()
    dispatcher["db"] = db
    dispatcher["config"] = config
    gate = SubscriptionGate(*config.gate_sources)
    dispatcher["gate"] = gate
    demo_state = DemoState(db, config.demo_ttl_minutes)
    dispatcher["demo"] = demo_state
    # Подписку проверяет основной бот — админом канала достаточно сделать его.
    dispatcher["gate_bot"] = bot

    throttle = ThrottleMiddleware(config.cooldown_seconds)
    subscription = SubscriptionMiddleware()
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.middleware(throttle)
        observer.middleware(subscription)

    # Раздел оценок: его роутеры идут ПЕРЕД основным, иначе фото и текст
    # перехватят обработчики отчётов и кода режима съёмки.
    from mograte.integration import attach_rate

    attach_rate(dispatcher, config, bot)

    dispatcher.include_router(handlers.router)

    tasks = []
    if config.webapp_enabled:
        registry = {token.split(":", 1)[0]: item for token, item in zip(config.bot_tokens, bots)}
        tasks.append(
            asyncio.create_task(
                _serve_webapp(config, db, demo_state, gate, bot, registry)
            )
        )
        logger.info("Мини-апп: %s (порт %s)", config.webapp_url, config.webapp_port)
    else:
        logger.info("Мини-апп выключен (WEBAPP_URL пуст)")

    try:
        hooked = False
        for index, current in enumerate(bots):
            await current.set_my_commands(COMMANDS)
            me = await current.get_me()
            role = "Основной бот" if index == 0 else f"Зеркало {index}"
            logger.info("%s запущен: @%s", role, me.username)

            if config.webapp_enabled:
                await current.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(
                        text="Приложение", web_app=WebAppInfo(url=config.webapp_url)
                    )
                )

            # Локальный режим работает на polling, а он несовместим с
            # вебхуком: Telegram разрешает что-то одно. Поэтому вебхук
            # приходится снять — но это выключает бота, поднятого на Vercel,
            # поэтому предупреждаем.
            hook = await current.get_webhook_info()
            hooked = hooked or bool(hook.url)
            await current.delete_webhook(drop_pending_updates=True)

        if len(bots) > 1:
            logger.info("Всего ботов: %s (основной + %s зеркал)", len(bots), len(bots) - 1)

        _log_setup(config)
        _warn_split_database(config)

        if hooked:
            logger.warning("=" * 70)
            logger.warning("Вебхуки сняты со всех ботов.")
            logger.warning(
                "Копии на хостинге перестанут отвечать, пока работает эта."
            )
            logger.warning(
                "Чтобы вернуть их: останови этот процесс и открой "
                "<адрес приложения>/api/setup?key=<SETUP_KEY>"
            )
            logger.warning("=" * 70)

        await dispatcher.start_polling(*bots)
    finally:
        for task in tasks:
            task.cancel()
        await db.close()
        from mograte.core import db as rate_db

        await rate_db.close()
        for current in bots:
            await current.session.close()
        logger.info("Бот остановлен")


async def _serve_webapp(config: Config, db, demo: DemoState, gate, bot, bots=None) -> None:
    """Мини-апп живёт в том же процессе и на том же event loop, что и бот."""
    import uvicorn

    from webapp.server import create_app

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config, db, demo, gate, bot, bots),
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
