"""فحص الاشتراك الإجباري والحدود اليومية."""
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .. import db
from ..config import DAILY_LIMIT_FREE, DAILY_LIMIT_VIP, OWNER_ID
from ..state import _LAST_OWNER_NOTIFY


async def sub_status(bot, user_id):
    """يعيد 'ok' أو سلسلة تشير لعدم الاشتراك."""
    if user_id == OWNER_ID:
        return "ok"
    channel_id = db.get_setting("channel_id")
    if not channel_id:
        return "ok"
    if db.is_vip(user_id):
        return "ok"
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        if member.status in ("member", "administrator", "creator"):
            return "ok"
        return "no"
    except Exception:
        now = time.time()
        if now - _LAST_OWNER_NOTIFY.get("channel", 0) > 300:
            _LAST_OWNER_NOTIFY["channel"] = now
            try:
                await bot.send_message(
                    OWNER_ID,
                    "⚠️ تعذر فحص القناة. تأكد أن البوت مشرف في القناة وأن المعرف صحيح.",
                )
            except Exception:
                pass
        return "ok"


async def join_prompt(bot, user_id, chat_id):
    name = db.get_setting("channel_name") or "القناة"
    url = db.get_setting("channel_url")
    if not url:
        url = f"https://t.me/{name}" if name else ""
    rows = []
    if url:
        rows.append([InlineKeyboardButton(f"📢 إنضم إلى {name}", url=url)])
    rows.append([InlineKeyboardButton("✅ تحققت, فحص الاشتراك", callback_data="verify:sub")])
    await bot.send_message(
        chat_id,
        "⚠️ <b>مطلوب اشتراك إجباري!</b>\n"
        "لإستخدام البوت يجب أن تكون مشتركاً في القناة أولاً، ثم اضغط زر التحقق.\n"
        "🎁 <i>اعضاء VIP معفون من هذا الشرط.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def check_limits(uid):
    """يعيد (ok, message)."""
    u = db.get_user(uid)
    if not u:
        return True, ""
    if u["is_banned"]:
        return False, "⛔ أنت محظور من استخدام هذا البوت."
    if u["is_vip"]:
        limit = int(db.get_setting("daily_limit_vip", str(DAILY_LIMIT_VIP)))
    else:
        limit = int(db.get_setting("daily_limit_free", str(DAILY_LIMIT_FREE)))
    daily = db.daily_download_count(uid)
    if daily >= limit:
        return False, f"📊 وصلت للحد اليومي ({limit} تحميلات).\nارجع غداً أو ترقى إلى VIP 👑"
    return True, ""
