"""طابور التحميل وتنفيذ مهام التنزيل والإرسال."""
import asyncio
import logging
import os
import traceback
from telegram import InputFile

from . import db
from .config import POINTS_PER_AUDIO, POINTS_PER_VIDEO, VIP_THRESHOLD
from .downloader import (DownloadError, cleanup, download_audio, download_video,
                          ensure_telegram_compatible, probe_media)
from .handlers.system import esc
from . import state
from .state import _USER_BUSY

logger = logging.getLogger(__name__)


def enqueue(user_id, coro):
    state._job_seq += 1
    priority = 0 if db.is_vip(user_id) else 1
    state._job_queue.put_nowait((priority, state._job_seq, coro))
    logger.info("🎯 أُضيفت مهمة إلى الطابور: user=%s seq=%s", user_id, state._job_seq)


async def queue_worker(app):
    """Processes download jobs; exits when the application stops."""
    # لا نعتمد على app.running لأنه يكون False وقت post_init،
    # فيخرج العامل فوراً ولا يعالج أي مهمة. نتوقف فقط عند إلغاء المهمة.
    while True:
        try:
            _prio, _seq, coro = await asyncio.wait_for(state._job_queue.get(), timeout=0.5)
            try:
                await coro()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Job crashed:\n%s", traceback.format_exc())
            finally:
                state._job_queue.task_done()
        except asyncio.CancelledError:
            break
        except asyncio.TimeoutError:
            continue
        except Exception:
            logger.error("Queue error:\n%s", traceback.format_exc())
            await asyncio.sleep(0.5)


async def schedule_download(*, bot, chat_id, uid, url, platform, kind, status_id):
    """جدولة تحميل + رفع للتيليغرام. kind = 'audio' | 'video'."""
    _USER_BUSY[uid] = True
    points = POINTS_PER_VIDEO if kind == "video" else POINTS_PER_AUDIO

    async def do_job():
        try:
            logger.info("⏳ بدء مهمة تحميل: platform=%s kind=%s", platform, kind)
            # منصات التواصل أسرع فشلاً إن كانت الشبكة/المنصة تمنع الطلب
            timeout = 150 if platform in ("instagram", "tiktok", "facebook") else 300
            if kind == "audio":
                path, title = await asyncio.wait_for(
                    asyncio.to_thread(download_audio, url), timeout=timeout
                )
            else:
                u = db.get_user(uid)
                max_h = 1080 if (u and u["is_vip"]) else 720
                path, title = await asyncio.wait_for(
                    asyncio.to_thread(download_video, url, max_h), timeout=timeout
                )

            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_id,
                text="📦 تم التحميل، جاري الرفع إلى تيليغرام…",
                parse_mode="HTML",
            )

            icon = "🎵" if kind == "audio" else "🎬"
            caption = f"{icon} <b>{esc(title or 'وسائط')}</b>\n<i>via {platform}</i>"
            size = os.path.getsize(path)
            try:
                if kind == "video":
                    # تأكد أن الصيغة يدعمها تيليجرام (حوّل للـ MP4)؛ إن فشل التحويل
                    # لا نرسل الملف الأصلي التالف بل نبلغ المستخدم.
                    try:
                        converted = await asyncio.to_thread(ensure_telegram_compatible, path)
                        test_note = "🧪 اختبار: النسخة المعبأة (remux H.264/AAC) — هل تشتغل عندك؟"
                    except DownloadError as conv_exc:
                        logger.warning("تحويل الفيديو فشل: %s", conv_exc)
                        raise conv_exc
                    if converted != path:
                        logger.info("✅ تحويل الفيديو إلى صيغة H.264 التي يدعمها تيليجرام: %s", converted)
                        path = converted
                    caption = f"{caption}\n{test_note}"
                    probe = await asyncio.to_thread(probe_media, path)
                    kwargs = {"supports_streaming": True, "parse_mode": "HTML"}
                    if probe:
                        kwargs.update(
                            duration=probe["duration"],
                            width=probe["width"],
                            height=probe["height"],
                        )
                    await bot.send_video(
                        chat_id, video=InputFile(path), caption=caption, **kwargs
                    )
                else:
                    await bot.send_audio(
                        chat_id, audio=InputFile(path), caption=caption,
                        title=title or "أغنية", parse_mode="HTML",
                    )
            finally:
                cleanup(path)

            old = db.get_user(uid) or {}
            was_vip = bool(old.get("is_vip"))
            db.add_download_stats(uid, kind, title, size)
            db.add_points(uid, points)
            now = db.get_user(uid) or {}
            logger.info("✅ اكتمل التحميل: %s", title)
            if not was_vip and now.get("is_vip"):
                await bot.send_message(
                    chat_id,
                    f"🎉 <b>مبروك! وصلت إلى {VIP_THRESHOLD} نقطة وأصبحت عضواً VIP!</b>\n"
                    "✅ إلغاء الاشتراك الإجباري\n"
                    "✅ جودة 1080p\n"
                    "✅ أولوية أعلى في التحميل",
                    parse_mode="HTML",
                )
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_id,
                text=f"✅ تم!\n⭐ +{points} نقطة (نقاطك: {now.get('points', 0)})",
                parse_mode="HTML",
            )
        except asyncio.TimeoutError:
            logger.warning("⏱️ انتهت مهلة التحميل")
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_id,
                    text="⏳ انتهت مهلة التحميل (الشبكة بطيئة أو المنصة حجبت الطلب).\n"
                    "جرّب مرة أخرى بعد قليل، أو أرسل رابطاً آخر.",
                    parse_mode="HTML",
                )
            except Exception:
                await bot.send_message(
                    chat_id,
                    "⏳ انتهت مهلة التحميل (الشبكة بطيئة أو المنصة حجبت الطلب).\n"
                    "جرّب مرة أخرى بعد قليل، أو أرسل رابطاً آخر.",
                )
        except DownloadError as exc:
            logger.warning("DownloadError: %s", exc)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=status_id, text=f"❌ {exc}", parse_mode="HTML"
                )
            except Exception:
                await bot.send_message(chat_id, f"❌ {exc}")
        except Exception:
            logger.error("Download failed:\n%s", traceback.format_exc())
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_id,
                    text="❌ حدث خطأ غير متوقع أثناء التحميل. حاول لاحقاً.",
                    parse_mode="HTML",
                )
            except Exception:
                try:
                    await bot.send_message(
                        chat_id,
                        "❌ حدث خطأ غير متوقع أثناء التحميل. حاول لاحقاً.",
                    )
                except Exception:
                    pass
        finally:
            _USER_BUSY[uid] = False
            logger.info("🏁 انتهت مهمة التحميل")

    enqueue(uid, do_job)
