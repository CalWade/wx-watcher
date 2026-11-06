"""Wall and monotonic clocks used by production adapters."""

import time
from datetime import UTC, datetime


class SystemClock:
    def timestamp(self):
        return datetime.now(UTC).isoformat()

    def time(self):
        return time.time()

    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)
