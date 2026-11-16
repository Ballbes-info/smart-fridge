"""Bounded inference serialization across threads and processes on one host."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import threading
import time

_waiting = threading.BoundedSemaphore(8)


class QueueBusy(Exception):
    pass


def wait_seconds():
    try:
        return max(0, min(60, int(os.getenv('GIGACHAT_QUEUE_WAIT_SECONDS', '25'))))
    except ValueError:
        return 25


@contextmanager
def inference_slot():
    if not _waiting.acquire(blocking=False):
        raise QueueBusy('Все места ожидания ИИ заняты. Повторите через несколько секунд.')
    handle = None
    acquired = False
    try:
        folder = Path(os.getenv('GIGACHAT_QUEUE_DIR', '').strip() or Path(__file__).resolve().parents[1] / 'instance')
        folder.mkdir(parents=True, exist_ok=True)
        handle = os.fdopen(os.open(folder / 'gigachat-inference.lock', os.O_CREAT | os.O_RDWR, 0o600), 'r+b')
        if not os.fstat(handle.fileno()).st_size:
            handle.write(b'\0')
            handle.flush()
        deadline = time.monotonic() + wait_seconds()
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise QueueBusy('ИИ занят другим запросом. Повторите через несколько секунд.') from None
                time.sleep(min(.05, remaining))
        yield
    finally:
        try:
            if handle is not None:
                try:
                    if acquired:
                        handle.seek(0)
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
        finally:
            _waiting.release()
