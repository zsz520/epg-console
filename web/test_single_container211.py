import signal
import subprocess
import sys
import time
from django.test import SimpleTestCase


class SingleContainer211Tests(SimpleTestCase):
    def test_either_service_exit_stops_other_and_exits_nonzero(self):
        code="from docker.supervisor import supervise; import sys; sys.exit(supervise([[sys.executable,'-c','raise SystemExit(0)'],[sys.executable,'-c','import time; time.sleep(30)']],grace=1))"
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,1)

    def test_sigterm_exits_cleanly(self):
        code="from docker.supervisor import supervise; import sys; sys.exit(supervise([[sys.executable,'-c','import time; time.sleep(30)']],grace=1))"
        process=subprocess.Popen([sys.executable,'-u','-c',code])
        try:
            time.sleep(.5)
            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=5),0)
        finally:
            if process.poll() is None:process.kill();process.wait()
