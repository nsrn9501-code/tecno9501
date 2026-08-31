"""فحص الاشتراك الإجباري والحدود اليومية."""
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .. import db
from ..config import DAILY_LIMIT_FREE, DAILY_LIMIT_VIP, OWNER_ID
from ..state import _LAST_OWNER_NOTIFY

logger = logging.getLogger(__name__)


async def sub_status(bot, user_id):
    """يعيد 'ok' أو 'no' (مالك أو VIP = ok دوماً)."""
    try:
        if user_id == OWNER_ID:
            return "ok"
        channels = db.get_subscription_channels()
        if not channels:
            return "ok"
        if db.is_vip(user_id):
            return "ok"
        for ch in channels:
            try:
                member = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
                if member.status not in ("member", "administrator", "creator"):
                    return "no"
            except Exception as e:
                logger.warning("⚠️ فشل فحص القناة %s (%s) للمستخدم %s: %s", ch["name"], ch["id"], user_id, e)
                now = time.time()
                if now - _LAST_OWNER_NOTIFY.get(f"channel:{ch['id']}", 0) > 300:
                    _LAST_OWNER_NOTIFY[f"channel:{ch['id']}"] = now
                    try:
                        await bot.send_message(
                            OWNER_ID,
                            f"⚠️ تعذر فحص القناة {ch['name']} ({ch['id']}).\n"
                            "تأكد أن البوت مشرف فيها والمعرف صحيح.",
                        )
                    except Exception:
                        pass
                return "no"
        return "ok"
    except Exception as e:
        logger.error("❌ خطأ حرج في sub_status: %s", e)
        return "ok"


async def join_prompt(bot, user_id, chat_id):
    """يرسل رسالة تحتوي على أزرار الانضمام لجميع القنوات المطلوبة."""
    channels = db.get_subscription_channels()
    if not channels:
        return
    rows = []
    for ch in channels:
        url = ch.get("url", "")
        name = ch.get("name", "القناة")
        if url:
            rows.append([InlineKeyboardButton(f"📢 إنضم إلى {name}", url=url)])
        else:
            rows.append([InlineKeyboardButton(f"📢 إنضم إلى {name}", url=f"https://t.me/{ch['id']}")])
    rows.append([InlineKeyboardButton("✅ تحققت, فحص الاشتراك", callback_data="verify:sub")])
    await bot.send_message(
        chat_id,
        "⚠️ <b>مطلوب اشتراك إجباري!</b>\n"
        "لإستخدام البوت يجب أن تكون مشتركاً في جميع القنوات التالية أولاً، ثم اضغط زر التحقق.\n"
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
