"""إرسال الملفات (فيديو) عبر Bot API HTTP مباشرة بـ `requests`.
هذه الطريقة أثبتت نجاحها في تشغيل الفيديو داخل تيليجرام (اختبار 11)
بدلاً من مكتبة python-telegram-bot التي كانت تُرسل فيديو يطلب مشغل خارجي."""
import requests

from .config import BOT_TOKEN

API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_video(chat_id, path, caption, duration=0, width=0, height=0):
    """يرسل فيديو عبر sendVideo مباشرة بـ HTTP multipart (نفس اختبار 11 الناجح)."""
    filename = str(path).split("/")[-1] or "video.mp4"
    data = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "HTML",
        "supports_streaming": True,
    }
    if duration:
        data["duration"] = int(duration)
    if width:
        data["width"] = int(width)
    if height:
        data["height"] = int(height)
    with open(path, "rb") as f:
        files = {"video": (filename, f, "video/mp4")}
        return requests.post(f"{API}/sendVideo", data=data, files=files, timeout=300)


def send_audio(chat_id, path, caption, title=None):
    """يرسل صوت عبر sendAudio مباشرة بـ HTTP multipart."""
    filename = str(path).split("/")[-1] or "audio.mp3"
    data = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if title:
        data["title"] = title
    with open(path, "rb") as f:
        files = {"audio": (filename, f, "audio/mpeg" if str(path).endswith(".mp3") else "application/octet-stream")}
        return requests.post(f"{API}/sendAudio", data=data, files=files, timeout=300)


def send_message(chat_id, text):
    """يرسل رسالة نصية HTML."""
    return requests.post(
        f"{API}/sendMessage",
        data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=60,
    )


def send_video_via_channel(channel_id, user_chat_id, path, caption, duration=0, width=0, height=0):
    """يرفع الفيديو إلى قناة أولاً (فيخزّنه ويعالجه سيرفر تيليجرام كفيديو
    رسمي قابل للبث)، ثم يرسله للمستخدم — فيعمل المشغل الداخلي دائماً.
    يعيد (status, message) — أو None إن لم تُفعّل قناة."""
    if not channel_id:
        return None
    # 1) ارفع للقناة
    sent = send_video(channel_id, path, caption, duration, width, height)
    if sent.status_code != 200:
        return (sent.status_code, sent.text)
    try:
        msg = sent.json()["result"]
        # فيديو معالَج من سيرفر تيليجرام — يُخزَّن في القناة فيصبح قابلاً للبث
        file_id = msg.get("video", {}).get("file_id")
        mid = msg["message_id"]
    except Exception:
        return (500, "failed to parse channel send")
    # 2) أولاً: إرسال مباشر للمستخدم بـ file_id المعالج (أضمن طريقة للبث الداخلي)
    if file_id:
        r = send_video_by_file_id(user_chat_id, file_id, caption, duration, width, height)
        if r.status_code == 200:
            return (200, "sent_by_file_id")
    # 3) ثانياً: نسخ رسالة القناة للمستخدم (copyMessage) — يحافظ على الملف المعالج
    #    ولا يظهر شريط "Forwarded" للمستخدم.
    cpy = copy_message(channel_id, user_chat_id, mid)
    if cpy.status_code == 200:
        return (200, "copied")
    # 4) أخيراً: إعادة توجيه رسالة القناة
    fwd = forward_message(channel_id, user_chat_id, mid)
    if fwd.status_code == 200:
        return (200, "forwarded")
    return (fwd.status_code, fwd.text)


def send_video_by_file_id(chat_id, file_id, caption, duration=0, width=0, height=0):
    """يرسل فيديو مُعالَج مسبقاً (file_id) من قناة رفع — لا رفع مباشر،
    فيضمن تشغيله داخل مشغل تيليجرام الداخلي على كل الأجهزة."""
    data = {
        "chat_id": chat_id,
        "video": file_id,
        "caption": caption,
        "parse_mode": "HTML",
        "supports_streaming": True,
    }
    if duration:
        data["duration"] = int(duration)
    if width:
        data["width"] = int(width)
    if height:
        data["height"] = int(height)
    return requests.post(f"{API}/sendVideo", data=data, timeout=120)


def copy_message(from_chat_id, to_chat_id, message_id):
    """ينسخ رسالة (فيديو) من قناة إلى المستخدم بدون شريط Forwarded —
    يبقي الملف المعالج من سيرفر تيليجرام فيعمل المشغل الداخلي."""
    return requests.post(
        f"{API}/copyMessage",
        data={"chat_id": to_chat_id, "from_chat_id": from_chat_id, "message_id": message_id},
        timeout=120,
    )


def forward_message(from_chat_id, to_chat_id, message_id):
    """يعيد توجيه رسالة (فيديو) من قناة إلى المستخدم — يضمن التشغيل الداخلي."""
    return requests.post(
        f"{API}/forwardMessage",
        data={"chat_id": to_chat_id, "from_chat_id": from_chat_id, "message_id": message_id},
        timeout=120,
    )


def edit_message_text(chat_id, message_id, text):
    """يعدّل نص رسالة الحالة."""
    return requests.post(
        f"{API}/editMessageText",
        data={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"},
        timeout=60,
    )


def edit_message_caption(chat_id, message_id, caption):
    """يعدّل نص/كابشن رسالة (مثلاً فيديو في قناة) — parse_mode HTML."""
    return requests.post(
        f"{API}/editMessageCaption",
        data={"chat_id": chat_id, "message_id": message_id, "caption": caption, "parse_mode": "HTML"},
        timeout=60,
    )


def get_message_id_from_send(resp):
    """يستخرج message_id من رد sendVideo (requests.Response)."""
    try:
        return resp.json()["result"]["message_id"]
    except Exception:
        return None


def upload_video_to_channel(channel_id, path, caption, duration=0, width=0, height=0):
    """يرفع الفيديو إلى قناة الرفع ويعيد (message_id, response) أو (None, resp)."""
    sent = send_video(channel_id, path, caption, duration, width, height)
    mid = get_message_id_from_send(sent)
    return (mid, sent)
