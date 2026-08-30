"""حالة ذاكرة مشتركة بين وحدات البوت."""
import asyncio

_job_queue: asyncio.PriorityQueue = None
_job_seq = 0
_USER_BUSY = {}          # user_id -> True
_OWNER_STATE = {}        # owner_id -> pending action
_SEARCH_RESULTS = {}     # user_id -> list of search results
_LAST_OWNER_NOTIFY = {}  # rate-limit for channel errors


def init_queue():
    global _job_queue, _job_seq
    _job_queue = asyncio.PriorityQueue()
    _job_seq = 0


# تتبّع إرسال الروابط للتقييد: user_id -> {"times": [unix...], "recent_urls": {normalized_url: unix}}
_RATE_URLS = {}

# روابط بانتظار اختيار الجودة: user_id -> {"url", "platform", "kind"}
_PENDING_LINKS = {}
