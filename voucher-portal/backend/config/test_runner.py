"""Test runner that waits for the app's own background workers.

`voucher_portal` assembles voucher PDFs in a daemon thread (see
services/generation.py), so a test that triggers generation returns before
that thread does. The thread holds its own database connection, and Postgres
refuses to drop a database that still has sessions on it - so teardown fails
with "database is being accessed by other users" and the whole run exits
non-zero even though every test passed.

SQLite doesn't enforce that, which is exactly why this only appears in CI.
Waiting here mirrors what happens in production, where the thread finishes on
its own and closes its connection in a `finally`.
"""
import threading
import time

from django.db import connections
from django.test.runner import DiscoverRunner

JOIN_TIMEOUT_SECONDS = 30


class BackgroundAwareRunner(DiscoverRunner):
    def teardown_databases(self, old_config, **kwargs):
        deadline = time.monotonic() + JOIN_TIMEOUT_SECONDS
        for thread in threading.enumerate():
            if thread in (threading.current_thread(), threading.main_thread()) or not thread.is_alive():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        connections.close_all()
        super().teardown_databases(old_config, **kwargs)
