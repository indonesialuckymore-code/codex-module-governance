"""Crash-safe serialization with conservative compatibility for legacy PID markers."""
import fcntl
import json
import os
from contextlib import contextmanager


@contextmanager
def process_lock(path, write_marker, error_type, error_code):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never unlink the advisory inode: another process may already be waiting on it.
    with path.with_name(path.name + '.advisory').open('a+') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise error_type(error_code) from error
        try:
            if path.exists():
                try:
                    value = json.loads(path.read_text())
                    pid = value['pid']
                    if type(pid) is not int or pid <= 0:
                        raise ValueError('invalid pid')
                    os.kill(pid, 0)
                except ProcessLookupError:
                    # Only a proven dead owner can have its transient marker recovered.
                    path.unlink()
                except (OSError, ValueError, KeyError, TypeError) as error:
                    raise error_type(error_code) from error
                else:
                    raise error_type(error_code)
            write_marker()
            try:
                yield
            finally:
                path.unlink(missing_ok=True)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
