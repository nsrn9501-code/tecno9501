"""نقطة تجميع البوت: بناء التطبيق وتسجيل الـ handlers."""
import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    Defaults,
    MessageHandler,
    filters,
)

from . import db, downloader, state, cache
from .config import BOT_TOKEN, OWNER_ID
from .handlers import callback
from .handlers.help import cmd_daily, cmd_help, cmd_referral
from .handlers.media import handle_text
from .handlers.owner import cmd_owner
from .handlers.start import cmd_start
from .jobs import queue_worker

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(app: Application):
    db.init_db()
    cache.init_cache()
    me = await app.bot.get_me()
    app.bot_data["username"] = me.username
    app.bot_data["bot_name"] = me.first_name
    logger.info("Bot @%s started", me.username)
    asyncio.create_task(queue_worker(app))
    downloader.cleanup_old_files(24)



async def _handle_owner_all(update, context):
    """يلقط جميع رسائل المالك (نص + فيديو + معاد توجيهها + أي نوع)
    ويرسلها لـ handle_owner_input إذا كان المالك في حالة انتظار."""
    uid = (update.effective_user or {}).id if update.effective_user else None
    if uid != OWNER_ID:
        return
    from .handlers.system import get_owner_state
    if get_owner_state(uid):
        from .handlers.owner import handle_owner_input
        await handle_owner_input(update, context)


def build_app():
    state.init_queue()
    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .defaults(defaults)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )

    # Holder for owner forwarded messages (catch ALL message types)
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND & filters.User(user_id=OWNER_ID),
        _handle_owner_all,
    ), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("daily", cmd_daily))
    app.add_handler(CommandHandler("referral", cmd_referral))
    app.add_handler(CommandHandler("owner", cmd_owner))
    app.add_handler(CommandHandler("cancel", callback.cancel_cmd))
    app.add_handler(CallbackQueryHandler(callback.on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app


if __name__ == "__main__":
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)
