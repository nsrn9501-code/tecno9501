import asyncio  # noqa: F401 (مطلوب للتوافق)

# عدد العمال المتوازيين الذين يعالجون التحميل في نفس الوقت
# هذا هو المفتاح لدعم 200 مستخدم: عدة مهام تُنفذ بالتوازي بدل واحد.
NUM_WORKERS = 6
# حد أعلى للمهام المتزامنة الفعلية (يمنع إغراق تيليجرام والشبكة)
MAX_CONCURRENT_JOBS = 8
_job_queue = None
_job_seq = 0
_USER_BUSY = {}
_OWNER_STATE = {}
_SEARCH_RESULTS = {}
_LAST_OWNER_NOTIFY = {}

# عدّاد المهام المفعّلة حالياً (Semaphore في الواقع - يُعرّف لاحقاً)
_job_sem = None


def init_queue():
    global _job_queue, _job_seq, _job_sem
    _job_queue = asyncio.PriorityQueue()
    _job_seq = 0
    _job_sem = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


def get_sem():
    return _job_sem


_RATE_URLS = {}

_PENDING_LINKS = {}
