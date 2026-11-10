import logging
import signal

from .config import settings
from .db import init_db
from .jobs import worker_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

_stop = False


def _sig(*_):
    global _stop
    _stop = True


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    init_db()
    logging.getLogger("hub.worker").info("worker started")
    worker_loop(stop=lambda: _stop, threads=max(1, settings().worker_threads))
