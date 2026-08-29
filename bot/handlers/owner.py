"""لوحة تحكم المالك: إحصائيات، إذاعة، قناة، نقاط، حظر، رابط هدية."""
import asyncio
import logging
import traceback

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .. import db
from ..config import DAILY_LIMIT_FREE, DAILY_LIMIT_VIP, GIFT_POINTS, OWNER_ID
from ..state import _OWNER_STATE
from .system import (
    clear_owner_state,
    esc,
    get_owner_state,
    send_stats_message,
    set_owner_state,
)

logger = logging.getLogger(__name__)


def owner_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 إحصائيات", callback_data="own:stats"),
                InlineKeyboardButton("👤 حسابي", callback_data="own:me"),
            ],
            [
                InlineKeyboardButton("📢 إذاعة", callback_data="own:broadcast"),
                InlineKeyboardButton("📌 إذاعة مثبتة", callback_data="own:pin"),
            ],
            [
                InlineKeyboardButton("🔐 قناة الاشتراك", callback_data="own:channel"),
                InlineKeyboardButton("⚙️ الإعدادات", callback_data="own:settings"),
            ],
            [
                InlineKeyboardButton("💬 كروب المناقشة", callback_data="own:group"),
            ],
            [
                InlineKeyboardButton("👑 منح نقاط", callback_data="own:points"),
                InlineKeyboardButton("🚫 حظر / فك", callback_data="own:ban"),
            ],
            [
                InlineKeyboardButton("🎁 رابط هدية", callback_data="own:gift"),
                InlineKeyboardButton("👥 أفضل المستخدمين", callback_data="own:top"),
            ],
            [InlineKeyboardButton("📥 حدود التحميل", callback_data="own:limits")],
            [
                InlineKeyboardButton("🛑 إيقاف البوت", callback_data="own:off"),
                InlineKeyboardButton("🔄 تشغيل البوت", callback_data="own:on"),
            ],
        ]
    )


async def cmd_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ هذه اللوحة للمالك فقط.")
        return
    await update.message.reply_text(
        "👑 <b>لوحة تحكم المالك</b>\nاختر ما تريد:", parse_mode=ParseMode.HTML,
        reply_markup=owner_keyboard(),
    )


async def _send_owner_panel_msg(bot, chat_id):
    await bot.send_message(
        chat_id, "👑 <b>لوحة تحكم المالك</b>", parse_mode=ParseMode.HTML,
        reply_markup=owner_keyboard(),
    )


async def handle_owner_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    action = get_owner_state(uid)
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id

    if action == "broadcast":
        clear_owner_state(uid)
        users = db.all_users()
        target_ids = [u["id"] for u in users if not u["is_banned"]]
        sent = 0
        failed = 0
        await update.message.reply_text(f"📢 جاري الإرسال إلى {len(target_ids)} مستخدم…")
        for tid in target_ids:
            try:
                await context.bot.copy_message(
                    chat_id=tid, from_chat_id=chat_id, message_id=update.message.message_id
                )
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        await context.bot.send_message(
            OWNER_ID, f"✅ تمت الإذاعة:\n✓ نجح: {sent}\n✗ فشل: {failed}"
        )
    elif action == "pin":
        clear_owner_state(uid)
        channel_id = db.get_setting("channel_id")
        users = db.all_users()
        target_ids = [u["id"] for u in users if not u["is_banned"]]
        sent = 0
        failed = 0
        await update.message.reply_text(
            f"📌 جاري إرسال الإذاعة المثبتة إلى {len(target_ids)} مستخدم…"
        )
        for tid in target_ids:
            try:
                await context.bot.copy_message(
                    chat_id=tid, from_chat_id=chat_id, message_id=update.message.message_id
                )
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        # إن وُجدت قناة، ننشر ونثبت منشوراً فيها أيضاً
        pinned = ""
        if channel_id:
            try:
                sent_msg = await context.bot.copy_message(
                    chat_id=channel_id, from_chat_id=chat_id, message_id=update.message.message_id
                )
                try:
                    await context.bot.pin_chat_message(
                        chat_id=channel_id, message_id=sent_msg.message_id
                    )
                    pinned = "\n📌 تم أيضاً تثبيتها في القناة."
                except Exception:
                    pinned = "\n📨 نُشرت بالقناة لكن تعذر تثبيتها."
            except Exception:
                pinned = ""
        await context.bot.send_message(
            OWNER_ID,
            f"✅ تمت الإذاعة المثبتة:\n✓ نجح: {sent}\n✗ فشل: {failed}{pinned}",
        )
    elif action == "limits":
        clear_owner_state(uid)
        if text.strip().lower() == "remove":
            db.set_setting("daily_limit_free", str(DAILY_LIMIT_FREE))
            db.set_setting("daily_limit_vip", str(DAILY_LIMIT_VIP))
            await update.message.reply_text("✅ تمت إعادة الحدود الافتراضية.")
            return
        parts = text.split()
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            await update.message.reply_text(
                "❌ الصيغة غير صحيحة.\nأرسل: <code>الحدود 10 50</code> (عادي VIP)",
                parse_mode=ParseMode.HTML,
            )
            return
        free_l, vip_l = int(parts[0]), int(parts[1])
        db.set_setting("daily_limit_free", str(free_l))
        db.set_setting("daily_limit_vip", str(vip_l))
        await update.message.reply_text(
            f"✅ تم تحديث الحدود اليومية:\n• عادي: <b>{free_l}</b> تحميل\n• VIP: <b>{vip_l}</b> تحميل",
            parse_mode=ParseMode.HTML,
        )
    elif action == "channel":
        clear_owner_state(uid)
        if text.strip().lower() == "remove":
            db.set_setting("channel_id", "")
            db.set_setting("channel_name", "")
            db.set_setting("channel_url", "")
            await update.message.reply_text("✅ تمت إزالة الاشتراك الإجباري.")
            return
        raw = text.strip()
        channel_id = raw
        if raw.startswith("@"):
            channel_name = raw[1:]
            channel_id = channel_name
        elif raw.startswith("https://t.me/"):
            channel_name = raw.replace("https://t.me/", "").split("/")[0]
            channel_id = channel_name
        else:
            channel_name = ""
        try:
            chat = await context.bot.get_chat(channel_id)
            cid = str(chat.id)
            uname = chat.username or ""
            invite_url = ""
            try:
                invite = await context.bot.create_chat_invite_link(cid)
                invite_url = invite.invite_link
            except Exception:
                pass
            db.set_setting("channel_id", cid)
            db.set_setting("channel_name", f"@{uname}" if uname else chat.title or "")
            db.set_setting("channel_url", invite_url or (f"https://t.me/{uname}" if uname else ""))
            try:
                await context.bot.get_chat_member(cid, OWNER_ID)
                check_ok = "✅"
            except Exception:
                check_ok = "⚠️"
            await context.bot.send_message(
                OWNER_ID,
                f"✅ تم تعيين قناة الاشتراك الإجباري:\n"
                f"🆔 {cid}\n📛 {chat.title or '—'}\n"
                f"🔗 {invite_url or 'بدون رابط (قناة عامة)'}\n"
                f"فحص العضوية: {check_ok} (تأكد أن البوت مشرف في القناة)",
            )
        except Exception as exc:
            await context.bot.send_message(
                OWNER_ID,
                f"❌ تعذر تعيين القناة: {exc}\n\n"
                "تأكد أن البوت مشرف في القناة وأن المعرف صحيح.",
            )
    elif action == "points":
        clear_owner_state(uid)
        parts = text.split()
        if len(parts) != 2 or not parts[0].lstrip("-").isdigit() or not parts[1].lstrip("-").isdigit():
            await update.message.reply_text(
                "❌ الصيغة غير صحيحة.\nأرسل: <code>ID عدد_النقاط</code>", parse_mode=ParseMode.HTML
            )
            return
        target_id, points = int(parts[0]), int(parts[1])
        if not db.get_user(target_id):
            await update.message.reply_text(f"❌ المستخدم {target_id} غير موجود بقاعدة البيانات.")
            return
        db.add_points(target_id, points)
        u = db.get_user(target_id)
        status = " وتم ترقيته إلى VIP 👑" if u["is_vip"] else ""
        await update.message.reply_text(
            f"✅ تم تعديل نقاط المستخدم {target_id} بمقدار {points:+d}\n"
            f"نقاطه الآن: <b>{u['points']}</b>{status}",
            parse_mode=ParseMode.HTML,
        )
        try:
            await context.bot.send_message(
                target_id,
                f"⚙️ تم تعديل نقاطك من قبل الإدارة: {points:+d}\nنقاطك الآن: {u['points']}",
            )
        except Exception:
            pass
    elif action == "group_set":
        clear_owner_state(uid)
        db.set_setting("discussion_group", text)
        db.set_setting("group_enabled", "1")
        await update.message.reply_text(
            f"✅ تم تعيين كروب المناقشة وتشغيله:\n🔗 {text}\n\n"
            "يمكنك إيقافه أو تغييره لاحقاً من لوحة المالك.",
        )
    elif action == "gift":
        clear_owner_state(uid)
        # format: <max_uses> [points]
        parts = text.split()
        try:
            max_uses = int(parts[0])
            points = int(parts[1]) if len(parts) > 1 else GIFT_POINTS
        except (ValueError, IndexError):
            await update.message.reply_text(
                "❌ الصيغة غير صحيحة.\nأرسل: <code>5</code> (عدد الاستخدامات) أو <code>5 20</code> (استخدامات ونقاط)",
                parse_mode=ParseMode.HTML,
            )
            return
        username = context.bot_data.get("username", "YourBot")
        code = db.create_gift_link(OWNER_ID, max_uses, points)
        link = f"https://t.me/{username}?start=gift_{code}"
        await update.message.reply_text(
            "🎁 <b>رابط الهدية</b>\n\n"
            f"📥 عدد الاستخدامات: <b>{max_uses}</b>\n"
            f"⭐ نقاط لكل مستخدم: <b>{points}</b>\n"
            f"🔗 <code>{link}</code>\n\n"
            "رابط يعمل فقط لعدد الاستخدامات المحددة وبعدها يتعطل تلقائياً.",
            parse_mode=ParseMode.HTML,
        )
    elif action == "ban":
        clear_owner_state(uid)
        target_id = None
        raw = text.strip()
        if raw.isdigit():
            target_id = int(raw)
        elif raw.startswith("@") and update.message.reply_to_message is None:
            users = db.all_users()
            for u in users:
                if (u["username"] or "").lower() == raw[1:].lower():
                    target_id = u["id"]
                    break
        elif update.message.reply_to_message:
            target_id = update.message.reply_to_message.from_user.id
        if not target_id:
            await update.message.reply_text("❌ لم أجد المستخدم. أرسل ID أو @يوزرنيم أو رد على رسالته.")
            return
        u = db.get_user(target_id)
        if not u:
            await update.message.reply_text(f"❌ المستخدم {target_id} غير موجود.")
            return
        new_state = 0 if u["is_banned"] else 1
        db.set_banned(target_id, new_state)
        action_txt = "🚫 تم حظر" if new_state else "✅ تم فك الحظر"
        await update.message.reply_text(
            f"{action_txt} المستخدم <code>{target_id}</code>", parse_mode=ParseMode.HTML
        )
        if new_state:
            try:
                await context.bot.send_message(target_id, "⛔ لقد تم حظرك من استخدام هذا البوت.")
            except Exception:
                pass


async def owner_cb(q, context, action, uid, chat_id):
    if action == "stats":
        s = db.total_stats()
        txt = (
            "📊 <b>إحصائيات البوت</b>\n\n"
            f"👥 المستخدمون: <b>{s['users']}</b>\n"
            f"📥 إجمالي التحميلات: <b>{s['downloads']}</b>\n"
            f"🎵 صوتيات: <b>{s['audio']}</b>\n"
            f"🎬 فيديو: <b>{s['video']}</b>\n"
            f"👑 أعضاء VIP: <b>{s['vips']}</b>"
        )
        await q.message.reply_text(txt, parse_mode=ParseMode.HTML)
    elif action == "me":
        await send_stats_message(context.bot, chat_id, OWNER_ID)
    elif action == "broadcast":
        set_owner_state(uid, "broadcast")
        await q.message.reply_text(
            "📢 أرسل لي الرسالة (نص أو وسائط) وسأرسلها لجميع المستخدمين.\n"
            "لإلغاء: /cancel"
        )
    elif action == "pin":
        set_owner_state(uid, "pin")
        await q.message.reply_text(
            "📌 أرسل الرسالة (نص أو وسائط) وسأرسلها لجميع المستخدمين.\n"
            "إن وُجدت قناة، سأنشرها وأثبتها فيها أيضاً.\n"
            "لإلغاء: /cancel"
        )
    elif action == "channel":
        set_owner_state(uid, "channel")
        await q.message.reply_text(
            "🔐 أرسل معرف القناة (@ChannelName أو -100xxxxxxxxxx)\n\n"
            "⚠️ يجب أن يكون البوت <b>مشرفاً</b> في القناة.\n"
            "لإزالة الاشتراك الإجباري أرسل: <code>remove</code>\n"
            "لإلغاء: /cancel",
            parse_mode=ParseMode.HTML,
        )
    elif action == "settings":
        ch = db.get_setting("channel_id") or "غير مفعلة"
        txt = (
            "⚙️ <b>الإعدادات الحالية</b>\n\n"
            f"🔐 القناة: <code>{ch}</code>\n"
            f"📥 حد التحميل اليومي (عادي): {db.get_setting('daily_limit_free')}\n"
            f"📥 حد التحميل اليومي (VIP): {db.get_setting('daily_limit_vip')}\n\n"
            "لتعديل الحدود اضغط زر «📥 حدود التحميل» بالأعلى."
        )
        await q.message.reply_text(txt, parse_mode=ParseMode.HTML)
    elif action == "group":
        link = db.get_setting("discussion_group") or "لم يُعيَّن"
        enabled = db.get_setting("group_enabled", "0") == "1"
        state_txt = "▶️ شغّال" if enabled else "⏹️ موقوف"
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔗 تعيين كروب", callback_data="own:group:set")],
                [
                    InlineKeyboardButton("▶️ تشغيل", callback_data="own:group:on"),
                    InlineKeyboardButton("⏹️ إيقاف", callback_data="own:group:off"),
                ],
                [InlineKeyboardButton("🔙 رجوع", callback_data="own:settings")],
            ]
        )
        await q.message.reply_text(
            "💬 <b>كروب المناقشة</b>\n\n"
            f"🔗 الكروب: <code>{esc(link)}</code>\n"
            f"📍 الحالة: {state_txt}\n\n"
            "من هنا تتحكم بكروب المناقشة: تعيينه، تشغيله أو إيقافه.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    elif action == "group:set":
        set_owner_state(uid, "group_set")
        await q.message.reply_text(
            "💬 أرسل رابط أو @يوزرنيم أو معرف الكروب.\n"
            "لإلغاء: /cancel"
        )
    elif action == "group:on":
        db.set_setting("group_enabled", "1")
        await q.message.reply_text("✅ تم تشغيل كروب المناقشة.")
    elif action == "group:off":
        db.set_setting("group_enabled", "0")
        await q.message.reply_text("✅ تم إيقاف كروب المناقشة.")
    elif action == "points":
        set_owner_state(uid, "points")
        await q.message.reply_text(
            "👑 أرسل: <code>ID عدد_النقاط</code>\nمثال: <code>123456 50</code>\n"
            "لإلغاء: /cancel", parse_mode=ParseMode.HTML
        )
    elif action == "gift":
        set_owner_state(uid, "gift")
        await q.message.reply_text(
            "🎁 أرسل عدد الاستخدامات، واختيارياً عدد النقاط.\n"
            "مثال: <code>5</code> = 5 أشخاص × 10 نقاط\n"
            "أو <code>5 20</code> = 5 أشخاص × 20 نقطة\n"
            "لإلغاء: /cancel", parse_mode=ParseMode.HTML
        )
    elif action == "ban":
        set_owner_state(uid, "ban")
        await q.message.reply_text(
            "🚫 أرسل ID المستخدم أو @يوزرنيم، أو أعد توجيه رسالته.\n"
            "سيتم حظره إن لم يكن محظوراً وفك الحظر إن كان محظوراً.\n"
            "لإلغاء: /cancel"
        )
    elif action == "limits":
        set_owner_state(uid, "limits")
        await q.message.reply_text(
            "📥 لتعديل الحدود اليومية أرسل رسالة نصها:\n"
            "<code>الحدود 10 50</code> (عادي VIP)\n"
            "أو أرسل <code>remove</code> لإعادة الافتراضي.\n"
            "لإلغاء: /cancel",
            parse_mode=ParseMode.HTML,
        )
    elif action == "top":
        users = db.all_users()[:10]
        lines = ["👥 <b>أفضل المستخدمين</b>", ""]
        for i, u in enumerate(users, 1):
            lines.append(
                f"{i}. {esc(u['first_name'] or 'مستخدم')} (@{esc(u['username'] or '-')}) — "
                f"{u['points']} نقطة | {u['total_downloads']} تحميل"
            )
        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    elif action == "off":
        await q.message.reply_text("🛑 جاري إيقاف البوت…")
        asyncio.create_task(_shutdown(context.application))
    elif action == "on":
        await q.message.reply_text("🔄 جاري إعادة تشغيل البوت…")
        asyncio.create_task(_restart(context.application))


async def _shutdown(app):
    await asyncio.sleep(0.5)
    try:
        await app.stop_running()
    except Exception:
        pass


async def _restart(app):
    """يعيد تشغيل عملية البوت عبر سكربت restart.sh (يعمل مع watchdog)."""
    await asyncio.sleep(0.5)
    try:
        import subprocess
        import os
        subprocess.Popen(
            ["bash", os.path.join(os.path.dirname(__file__), "..", "..", "restart.sh")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass
    try:
        await app.stop_running()
    except Exception:
        pass
