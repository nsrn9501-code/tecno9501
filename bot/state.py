import asyncio  # noqa: F410 (مطلوب للتوافق)

# ═══════════════════════════════════════════════════════════════════════
#  إعدادات الأداء — محسّنة لدعم 1000+ مستخدم
# ═══════════════════════════════════════════════════════════════════════

# عدد العمال المتوازيين الذين يعالجون التحميل في نفس الوقت.
# كل عامل يحمّل فيديو/صوت بشكل مستقل. 15 عامل = 15 تحميل متزامن.
NUM_WORKERS = 15

# حد أعلى للمهام المتزامنة الفعلية (يمنع إغراق تيليجرام).
# 20 = أقصى حد آمن لتيليجرام Bot API بدون حظر.
MAX_CONCURRENT_JOBS = 20

_job_queue = None
_job_seq = 0
_USER_BUSY = {}
_OWNER_STATE = {}
_SEARCH_RESULTS = {}
_LAST_OWNER_NOTIFY = {}

# عدّاد المهام المفعّلة حالياً (Semaphore في الواقع)
_job_sem = None

# ═══════════════════════════════════════════════════════════════════════
#  تتبع الطابور — لعرض رقم الانتظار للمستخدم
# ═══════════════════════════════════════════════════════════════════════
_QUEUE_POSITIONS = {}  # uid -> approximate position in queue


def init_queue():
    global _job_queue, _job_seq, _job_sem
    _job_queue = asyncio.PriorityQueue()
    _job_seq = 0
    _job_sem = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


def get_sem():
    return _job_sem


def get_queue_position(uid):
    """يعيد رقم تقريبي لوضع المستخدم في الطابور."""
    return _QUEUE_POSITIONS.get(uid, 0)


def set_queue_position(uid, pos):
    _QUEUE_POSITIONS[uid] = pos


def clear_queue_position(uid):
    _QUEUE_POSITIONS.pop(uid, None)


_RATE_URLS = {}

_PENDING_LINKS = {}
