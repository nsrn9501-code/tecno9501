"""طابور التحميل وتنفيذ مهام التنزيل والإرسال."""
import asyncio
import logging
import os
import time
import traceback

from . import uploader

from . import db
from .config import POINTS_PER_AUDIO, POINTS_PER_VIDEO, VIP_THRESHOLD
from .downloader import (DownloadError, cleanup, download_audio, download_video,
                          ensure_telegram_compatible, probe_media)
from .facts import next_fact
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

async def schedule_download(*, bot, chat_id, uid, url, platform, kind, status_id, max_height=None):
    """جدولة تحميل + رفع للتيليغرام. kind = 'audio' | 'video'."""
    _USER_BUSY[uid] = True
    points = POINTS_PER_VIDEO if kind == "video" else POINTS_PER_AUDIO

    async def do_job():
        try:
            start_time = time.time()
            logger.info("⏳ بدء مهمة تحميل: platform=%s kind=%s", platform, kind)
            user = db.get_user(uid) or {}
            user_disp = (user.get("first_name") or "مستخدم")
            user_uname = user.get("username") or "—"
            # منصات التواصل أسرع فشلاً إن كانت الشبكة/المنصة تمنع الطلب
            timeout = 150 if platform in ("instagram", "tiktok", "facebook") else 300
            if kind == "audio":
                path, title = await asyncio.wait_for(
                    asyncio.to_thread(download_audio, url), timeout=timeout
                )
            else:
                u = db.get_user(uid)
                # جودة مختارة يدوياً: تحترمها دائماً، وإلا فـ 1080 لـ VIP و720 للعادي
                if max_height:
                    max_h = max_height
                else:
                    max_h = 1080 if (u and u["is_vip"]) else 720
                path, title = await asyncio.wait_for(
                    asyncio.to_thread(download_video, url, max_h), timeout=timeout
                )

            icon = "🎵" if kind == "audio" else "🎬"
            caption = f"{icon} <b>{esc(title or 'وسائط')}</b>\n<i>via {platform}</i>"
            size = os.path.getsize(path)
            try:
                if kind == "video":
                    # تأكد أن الصيغة يدعمها تيليجرام (حوّل للـ MP4 / H.264)؛
                    # لا نرسل أبداً ملفاً بترميز آخر (VP9/AV1) لأنه يطلب مشغلاً خارجياً.
                    try:
                        converted = await asyncio.to_thread(ensure_telegram_compatible, path)
                    except DownloadError as conv_exc:
                        logger.warning("تحويل الفيديو فشل: %s", conv_exc)
                        raise conv_exc
                    if converted != path:
                        logger.info("✅ تحويل الفيديو إلى صيغة MP4/H.264 يدعمها تيليجرام: %s", converted)
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                        path = converted
                    probe = await asyncio.to_thread(probe_media, path)
                    # شرط واحد فقط: الفيديو يجب أن يكون H.264/MP4. نترك الصوت كما هو
                    # (HE-AAC يعمل مع تيليجرام بعد التحويل بالظبط الأصل) — لا نرفضه.
                    if not probe or probe.get("vcodec") != "h264":
                        raise DownloadError(
                            "❌ الفيديو وصل بترميز لا يدعمه تيليجرام وفشل تحويله. جرّب رابطاً آخر."
                        )
                    # الطريقة المضمونة: إرسال عبر قناة رفع إن كانت مفعّلة
                    channel_id = db.get_setting("upload_channel_id", "").strip()
                    if channel_id:
                        up_kwargs = {}
                        if probe:
                            up_kwargs = {
                                "duration": probe["duration"],
                                "width": probe["width"],
                                "height": probe["height"],
                            }
                        elapsed_so_far = time.time() - start_time
                        chan_caption = (
                            f"{icon} <b>{esc(title or 'وسائط')}</b>\n"
                            f"<i>via {platform}</i>\n"
                            f"📺 من: @{esc(user_uname)} (<code>{uid}</code>)\n"
                            f"🗂 الحجم: {_fmt_size(size)}\n"
                            f"⏰ المعالجة: {elapsed_so_far:.1f} ثا\n"
                            f"🌐 المصدر: <code>{esc(url)}</code>"
                        )
                        chan_mid, resp = uploader.upload_video_to_channel(
                            channel_id, path, chan_caption, **up_kwargs
                        )
                        if chan_mid:
                            # ارفع للفيديو للمستخدم عبر copyMessage
                            cpy = uploader.copy_message(channel_id, chat_id, chan_mid)
                            sent_ok = (cpy.status_code == 200)
                        else:
                            sent_ok = False
                        if not sent_ok:
                            # حاول إرسال مباشر كخيار أخير
                            r = uploader.send_video(chat_id, path, caption, **up_kwargs)
                            if r.status_code != 200:
                                logger.error("فشل إرسال الفيديو: %s", r.text[:200])
                                raise DownloadError(
                                    "❌ تعذر إرسال الفيديو عبر قناة الرفع. تأكد من ضبطها في لوحة المالك."
                                )
                    else:
                        up_kwargs = {}
                        if probe:
                            up_kwargs = {
                                "duration": probe["duration"],
                                "width": probe["width"],
                                "height": probe["height"],
                            }
                        r = uploader.send_video(chat_id, path, caption, **up_kwargs)
                        if r.status_code != 200:
                            logger.error("فشل إرسال الفيديو: %s", r.text[:200])
                else:
                    r = uploader.send_audio(chat_id, path, caption, title=title or "أغنية")
                    if r.status_code != 200:
                        logger.error("فشل إرسال الصوت: %s", r.text[:200])
            finally:
                cleanup(path)

            old = db.get_user(uid) or {}
            was_vip = bool(old.get("is_vip"))
            db.add_download_stats(uid, kind, title, size)
            db.add_points(uid, points)
            now = db.get_user(uid) or {}
            logger.info("✅ اكتمل التحميل: %s", title)
            if not was_vip and now.get("is_vip"):
                uploader.send_message(
                    chat_id,
                    f"🎉 <b>مبروك! وصلت إلى {VIP_THRESHOLD} نقطة وأصبحت عضواً VIP!</b>\n"
                    "✅ إلغاء الاشتراك الإجباري\n"
                    "✅ جودة 1080p\n"
                    "✅ أولوية أعلى في التحميل",
                )
            fact_label, fact_text = next_fact(uid)
            uploader.edit_message_text(chat_id, status_id,
                f"✅ تم!\n⭐ +{points} نقطة (نقاطك: {now.get('points', 0)})\n\n"
                f"💡 <b>معلومة {esc(fact_label)}:</b>\n{esc(fact_text)}")
        except asyncio.TimeoutError:
            logger.warning("⏱️ انتهت مهلة التحميل")
            try:
                uploader.edit_message_text(chat_id, status_id,
                    "⏳ انتهت مهلة التحميل (الشبكة بطيئة أو المنصة حجبت الطلب).\n"
                    "جرّب مرة أخرى بعد قليل، أو أرسل رابطاً آخر.")
            except Exception:
                uploader.send_message(chat_id,
                    "⏳ انتهت مهلة التحميل (الشبكة بطيئة أو المنصة حجبت الطلب).\n"
                    "جرّب مرة أخرى بعد قليل، أو أرسل رابطاً آخر.")
        except DownloadError as exc:
            logger.warning("DownloadError: %s", exc)
            try:
                uploader.edit_message_text(chat_id, status_id, f"❌ {exc}")
            except Exception:
                uploader.send_message(chat_id, f"❌ {exc}")
        except Exception:
            logger.error("Download failed:\n%s", traceback.format_exc())
            try:
                uploader.edit_message_text(chat_id, status_id, "❌ حدث خطأ غير متوقع أثناء التحميل. حاول لاحقاً.")
            except Exception:
                try:
                    uploader.send_message(chat_id, "❌ حدث خطأ غير متوقع أثناء التحميل. حاول لاحقاً.")
                except Exception:
                    pass
        finally:
            _USER_BUSY[uid] = False
            logger.info("🏁 انتهت مهمة التحميل")

    enqueue(uid, do_job)


def _fmt_size(num):
    """يحوّل حجم الملف إلى مقروء (MB/GB)."""
    try:
        mb = num / (1024 * 1024)
        if mb >= 1024:
            return f"{mb/1024:.2f} GB"
        return f"{mb:.1f} MB"
    except Exception:
        return str(num)
