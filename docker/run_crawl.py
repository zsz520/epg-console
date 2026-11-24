"""Shared lock and hard timeout apply to scheduled and manual crawls."""
import fcntl
import os
import signal
import subprocess
import sys


def main(args=None):
    with open('/data/crawl.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('A crawl is already running; skipped.', flush=True)
            return 0
        timeout = max(10, int(os.getenv('CRAWL_TIMEOUT_SECONDS', '1800')))
        child = subprocess.Popen([sys.executable, 'main.py', *(args or [])], start_new_session=True)

        def stop(signum, frame):
            os.killpg(child.pid, signal.SIGTERM)

        previous = signal.signal(signal.SIGTERM, stop)
        try:
            try:
                return child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                print(f'Crawl exceeded {timeout}s; stopping this run.', flush=True)
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                return 124
        finally:
            signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
