"""
PythonAnywhere WSGI webhook gateway for the bot (Free tier).

KEY: PythonAnywhere kills daemon threads after the WSGI module loads, so a
persistent background asyncio loop does NOT survive between webhook requests.
The bot's httpx client is bound to whichever loop processes the corking.

Solution: do NOT persist an event loop. Build a fresh Application per webhook
request and run it via asyncio.run() (a fresh event loop each time). This is
slightly slower per request but 100% reliable on PythonAnywhere Free.

Proxies: PythonAnywhere routes outbound HTTP through a mandatory proxy.
Capture them here and restore before each request because bot/downloader.py
strips them at import (for HF Spaces compatibility).
"""
import sys, os, logging, zipfile, traceback

# ── Capture proxies BEFORE any bot import ────────────────────────────────
_PROXIES = {k: v for k, v in os.environ.items()
            if k.lower() in ('http_proxy', 'https_proxy', 'all_proxy',
                             'ftp_proxy', 'no_proxy', 'socks_proxy')}

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("passenger_wsgi")

# ── Paths + .env ─────────────────────────────────────────────────────────
_BOT_DIR = "/home/Nasr01/tecno9501"
_VENDOR = os.path.join(_BOT_DIR, "vendor")
_ENV_FILE = os.path.join(_BOT_DIR, ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Extract vendor.zip if needed
if not os.path.isdir(_VENDOR) or not os.listdir(_VENDOR):
    _ZIP = os.path.join(_BOT_DIR, "vendor.zip")
    if os.path.exists(_ZIP):
        os.makedirs(_VENDOR, exist_ok=True)
        with zipfile.ZipFile(_ZIP, "r") as zf:
            zf.extractall(_BOT_DIR)

for p in (_VENDOR, _BOT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from flask import Flask, request
flask_app = Flask(__name__)


def _restore_proxies():
    for k, v in _PROXIES.items():
        os.environ[k] = v


def _build_and_start():
    """Build a fresh Application, initialize+start+stop lifetime in one
    asyncio.run() call. Returns (ok, error)."""
    import asyncio
    _restore_proxies()

    async def _run():
        _restore_proxies()
        try:
            from telegram.constants import ParseMode
            from telegram.ext import (
                ApplicationBuilder, CallbackQueryHandler, CommandHandler,
                Defaults, MessageHandler, filters,
            )
            from bot.handlers import callback
            from bot.handlers.help import cmd_daily, cmd_help, cmd_referral
            from bot.handlers.media import handle_text
            from bot.handlers.owner import cmd_owner
            from bot.handlers.start import cmd_start
            from bot.main import post_init
            from bot import state

            _restore_proxies()
            state.init_queue()

            defaults = Defaults(parse_mode=ParseMode.HTML)
            app = (
                ApplicationBuilder()
                .token(os.environ["BOT_TOKEN"])
                .defaults(defaults)
                .post_init(post_init)
                .concurrent_updates(True)
                .read_timeout(60)
                .write_timeout(60)
                .connect_timeout(30)
                .build()
            )
            _restore_proxies()
            await app.initialize()
            await app.start()
            logger.info("App ready: @%s", app.bot.username)
            return app

        except Exception:
            logger.exception("App boot FAILED")
            raise

    try:
        app = asyncio.run(_run())
        return app
    except Exception as e:
        return None


def _process_update(app, update_json):
    """Run process_update on a fresh event loop."""
    import asyncio
    from telegram import Update
    _restore_proxies()
    try:
        # Telegram can send updates missing first_name/username -> patch them
        if "message" in update_json and isinstance(update_json.get("message"), dict):
            _msg = update_json["message"]
            if "from" in _msg and isinstance(_msg["from"], dict):
                _msg["from"].setdefault("first_name", "")
                _msg["from"].setdefault("username", "")
        if "callback_query" in update_json and update_json.get("callback_query"):
            _cq = update_json["callback_query"]
            if "from" in _cq and isinstance(_cq["from"], dict):
                _cq["from"].setdefault("first_name", "")
                _cq["from"].setdefault("username", "")
        update = Update.de_json(update_json, app.bot)
        if update is None:
            return "bad update", 400
        asyncio.run(app.process_update(update))
        return "OK", 200
    except Exception:
        logger.exception("process_update FAILED")
        return "OK", 200  # consume to stop Telegram retry loops


# No persistence — build per request is heavy but reliable. For performance,
# we keep ONE app alive per PythonAnywhere worker reload window by storing it
# as a global that survives while the interpreter lives (uWSGI does NOT kill
# module-level globals between requests — only daemon threads).
_app_global = {"app": None}


import time as _time

def _get_app():
    app = _app_global["app"]
    if app is not None:
        return app
    # Retry boot a few times to ride out transient proxy 503s
    for _attempt in range(5):
        app = _build_and_start()
        if app is not None:
            _app_global["app"] = app
            return app
        if _attempt < 4:
            _time.sleep(2)
    return None


_TOKEN = os.environ["BOT_TOKEN"]


@flask_app.route(f"/{_TOKEN}", methods=["POST"])
def webhook_handler():
    data = request.get_json(force=True, silent=True)
    if not data:
        return "bad request", 400

    app = _get_app()
    if app is None:
        return "boot failed", 503

    return _process_update(app, data)


@flask_app.route("/", methods=["GET"])
def health():
    app = _app_global["app"]
    if app is not None:
        try:
            return f"Bot: running (@{app.bot.username})"
        except Exception:
            return "Bot: running"
    return "Bot: FAILED (not booted yet)\n<Request a webhook to trigger boot>"


application_ = flask_app
