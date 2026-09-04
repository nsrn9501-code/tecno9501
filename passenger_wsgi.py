"""
PythonAnywhere webhook gateway — persistent non-daemon event loop.
"""
import sys, os, logging, asyncio, threading, zipfile, traceback

_PROXIES = {k: v for k, v in os.environ.items()
            if k.lower() in ('http_proxy', 'https_proxy', 'all_proxy',
                             'ftp_proxy', 'no_proxy', 'socks_proxy')}

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("passenger_wsgi")

_BOT_DIR = "/home/Nasr01/tecno9501"
_VENDOR = os.path.join(_BOT_DIR, "vendor")
_ENV_FILE = os.path.join(_BOT_DIR, ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

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

_boot_ok = False
_boot_app = None
_boot_error = ""
_loop = asyncio.new_event_loop()

# NON-daemon: PythonAnywhere won't kill this thread between requests
_loop_thread = threading.Thread(target=_loop.run_forever, daemon=False)
_loop_thread.start()

def _boot():
    global _boot_ok, _boot_app, _boot_error
    if _boot_ok:
        return

    # Retry boot up to 5 times for transient proxy 503s
    for attempt in range(5):
        try:
            _restore_proxies()

            async def _start():
                _restore_proxies()
                from telegram.constants import ParseMode
                from telegram.ext import (
                    ApplicationBuilder, Defaults, MessageHandler,
                    CallbackQueryHandler, CommandHandler, filters,
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
                _restore_proxies()
                await app.start()
                logger.info("App ready: @%s", app.bot.username)
                return app

            future = asyncio.run_coroutine_threadsafe(_start(), _loop)
            app = future.result(timeout=120)
            _boot_ok = True
            _boot_app = app
            logger.info("Boot OK!")
            return
        except Exception:
            _boot_error = traceback.format_exc()
            logger.exception("Boot attempt %d FAILED", attempt + 1)
            import time
            time.sleep(2)

try:
    _boot()
except Exception:
    _boot_error = traceback.format_exc()

_TOKEN = os.environ["BOT_TOKEN"]
from telegram import Update

@flask_app.route(f"/{_TOKEN}", methods=["POST"])
def webhook_handler():
    if not _boot_ok or _boot_app is None:
        return "boot not ready", 503

    _restore_proxies()
    data = request.get_json(force=True, silent=True)
    if not data:
        return "bad request", 400

    # Patch missing first_name/username from Telegram
    for key in ("message", "callback_query"):
        obj = data.get(key)
        if isinstance(obj, dict) and "from" in obj and isinstance(obj["from"], dict):
            obj["from"].setdefault("first_name", "")
            obj["from"].setdefault("username", "")

    update = Update.de_json(data, _boot_app.bot)
    if update is None:
        return "bad update", 400

    async def _process():
        _restore_proxies()
        try:
            await _boot_app.process_update(update)
        except Exception:
            logger.exception("process_update FAILED")

    fut = asyncio.run_coroutine_threadsafe(_process(), _loop)
    try:
        fut.result(timeout=180)
    except Exception:
        logger.exception("process_update timeout/error")
    return "OK"

@flask_app.route("/", methods=["GET"])
def health():
    if _boot_ok and _boot_app:
        alive = _loop.is_running() and _loop_thread.is_alive()
        return f"Bot: running (@{_boot_app.bot.username})\nLoop: {'alive' if alive else 'DEAD'}"
    return f"Bot: FAILED\n{_boot_error[:300]}"

application_ = flask_app
