"""Run web and scheduler together; fail the container if either service exits."""
import os
import signal
import subprocess
import sys
import time


def supervise(commands, grace=15, restore_marker=None):
    children=[]
    stopping=False
    def stop(signum, frame):
        nonlocal stopping
        stopping=True
    for signum in (signal.SIGTERM,signal.SIGINT):
        signal.signal(signum,stop)
    code=0
    try:
        for command in commands:
            children.append(subprocess.Popen(command,start_new_session=True))
        while not stopping:
            if restore_marker and os.path.exists(restore_marker):
                print('Confirmed restore requested; stopping services for cold restart.',flush=True)
                code=75
                break
            if any(child.poll() is not None for child in children):
                print('Web or scheduler exited; stopping container for restart.',flush=True)
                code=1
                break
            time.sleep(.25)
    finally:
        for child in children:
            if child.poll() is None:
                try:os.killpg(child.pid,signal.SIGTERM)
                except ProcessLookupError:pass
        deadline=time.monotonic()+grace
        for child in children:
            try:child.wait(timeout=max(.01,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                child.wait()
    return code


def main(web_command):
    return supervise([web_command,[sys.executable,'docker/entrypoint.py','scheduler']],restore_marker='/data/restore-pending.json')
