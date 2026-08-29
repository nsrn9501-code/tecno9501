"""معالجة الـ inline callbacks + أمر الإلغاء."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .. import db
from ..config import OWNER_ID
from ..jobs import schedule_download
from ..state import _OWNER_STATE, _SEARCH_RESULTS, _USER_BUSY
from .owner import owner_cb
from .subscription import check_limits, sub_status
from .system import esc


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    data = q.data or ""
    chat_id = update.effective_chat.id

    if data == "cancel":
        _SEARCH_RESULTS.pop(uid, None)
        await q.edit_message_text("🚫 تم الإلغاء.")
        return

    if data.startswith("own:"):
        if uid != OWNER_ID:
            await q.answer("⛔ غير مصرح لك")
            return
        await owner_cb(q, context, data[4:], uid, chat_id)
        return

    if data == "verify:sub":
        status = await sub_status(context.bot, uid)
        if status == "ok":
            await q.edit_message_text(
                "✅ تم التحقق بنجاح! أرسل رابطاً للتحميل أو إسم أغنية للبحث.",
            )
        else:
            await q.answer("❌ لست مشتركاً بعد!", show_alert=True)
        return

    if data.startswith("pick:"):
        idx = int(data.split(":")[1])
        results = _SEARCH_RESULTS.get(uid)
        if not results or idx >= len(results):
            await q.edit_message_text("😕 انتهت صلاحية البحث، أعد المحاولة.")
            return
        r = results[idx]
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🎵 MP3 صوت", callback_data=f"fmt:a:{idx}"),
                    InlineKeyboardButton("🎬 MP4 فيديو", callback_data=f"fmt:v:{idx}"),
                ],
                [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")],
            ]
        )
        await q.edit_message_text(
            f"📥 <b>{esc(r['title'])}</b>\nاختر الصيغة:", parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return

    if data.startswith("fmt:"):
        _, kind, idx = data.split(":")
        idx = int(idx)
        results = _SEARCH_RESULTS.get(uid)
        if not results or idx >= len(results):
            await q.edit_message_text("😕 انتهت صلاحية البحث، أعد المحاولة.")
            return
        r = results[idx]
        url = f"https://www.youtube.com/watch?v={r['id']}"
        await q.edit_message_text(
            f"⏳ جاري تجهيز «{esc(r['title'])}»…\nتحميل {('صوتي 🎵' if kind=='a' else 'فيديو 🎬')}",
            parse_mode=ParseMode.HTML,
        )
        _SEARCH_RESULTS.pop(uid, None)

        ok, msg = check_limits(uid)
        if not ok:
            await context.bot.send_message(chat_id, msg)
            return
        if _USER_BUSY.get(uid):
            await context.bot.send_message(chat_id, "⏳ عندك طلب قيد التنفيذ، إنتظر رجاءً…")
            return

        await schedule_download(
            bot=context.bot, chat_id=chat_id, uid=uid, url=url,
            platform="youtube", kind=("audio" if kind == "a" else "video"),
            status_id=q.message.message_id,
        )
        return


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in _OWNER_STATE:
        del _OWNER_STATE[uid]
        await update.message.reply_text("🚫 تم الإلغاء.")
    else:
        await update.message.reply_text("لا يوجد إجراء قيد التنفيذ.")
