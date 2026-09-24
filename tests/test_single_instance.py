import os
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QCoreApplication

from src import single_instance
from src.single_instance import SHOW_MESSAGE, SingleInstanceServer, acquire_instance_lock

REPO_ROOT = Path(__file__).resolve().parent.parent


def unique_name():
    return f"Dash-test-{uuid.uuid4().hex[:12]}"


class LockTests(unittest.TestCase):
    def test_only_one_holder_at_a_time(self):
        name = unique_name()
        first = acquire_instance_lock(name)
        self.assertIsNotNone(first)
        self.assertIsNone(acquire_instance_lock(name))
        first.release()
        again = acquire_instance_lock(name)
        self.assertIsNotNone(again)
        again.release()

    def test_launches_at_the_same_moment_get_one_winner(self):
        name = unique_name()
        code = (
            "import sys, time\n"
            "from src.single_instance import acquire_instance_lock\n"
            f"lock = acquire_instance_lock({name!r})\n"
            "print('won' if lock else 'lost', flush=True)\n"
            "time.sleep(1.5)\n"
        )
        processes = [
            subprocess.Popen([sys.executable, "-c", code], cwd=REPO_ROOT, stdout=subprocess.PIPE, text=True,
                             env={**os.environ})
            for _ in range(4)
        ]
        outcomes = [process.communicate(timeout=30)[0].strip() for process in processes]
        self.assertEqual(sorted(outcomes), ["lost", "lost", "lost", "won"])

    def test_a_crashed_holder_does_not_block_the_next_start(self):
        name = unique_name()
        code = (
            "import os\n"
            "from src.single_instance import acquire_instance_lock\n"
            f"lock = acquire_instance_lock({name!r})\n"
            "os._exit(0)\n"  # no clean-up, like a crash
        )
        subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, check=True)
        lock = acquire_instance_lock(name)
        self.assertIsNotNone(lock)
        lock.release()


class NotifyTests(unittest.TestCase):
    def tearDown(self):
        single_instance.release_instance_lock()

    def test_the_first_copy_keeps_the_lock_and_runs(self):
        with mock.patch.object(single_instance, "server_name", return_value=unique_name()):
            self.assertFalse(single_instance.notify_running_instance())
            # Asking again from the same process does not lock itself out.
            self.assertFalse(single_instance.notify_running_instance())

    def test_a_later_copy_hands_over_and_exits(self):
        with mock.patch.object(single_instance, "acquire_instance_lock", return_value=None), \
                mock.patch.object(single_instance, "_send", return_value=True) as send:
            self.assertTrue(single_instance.notify_running_instance(SHOW_MESSAGE))
        send.assert_called_once_with(SHOW_MESSAGE)

    def test_a_later_copy_waits_for_a_first_copy_still_starting(self):
        answers = iter([False, False, True])
        with mock.patch.object(single_instance, "acquire_instance_lock", return_value=None), \
                mock.patch.object(single_instance, "_send", side_effect=lambda _m: next(answers)), \
                mock.patch.object(single_instance, "RETRY_INTERVAL_MS", 1):
            self.assertTrue(single_instance.notify_running_instance())

    def test_a_silent_holder_still_means_exit(self):
        with mock.patch.object(single_instance, "acquire_instance_lock", return_value=None), \
                mock.patch.object(single_instance, "_send", return_value=False), \
                mock.patch.object(single_instance, "STARTUP_WAIT_MS", 20), \
                mock.patch.object(single_instance, "RETRY_INTERVAL_MS", 1):
            self.assertTrue(single_instance.notify_running_instance())


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_show_message_reaches_the_running_copy(self):
        name = unique_name()
        received = []
        with mock.patch.object(single_instance, "server_name", return_value=name):
            server = SingleInstanceServer(received.append)
            self.assertTrue(server.listening)
            code = (
                "from src import single_instance\n"
                f"single_instance.server_name = lambda: {name!r}\n"
                "raise SystemExit(0 if single_instance._send(single_instance.SHOW_MESSAGE) else 1)\n"
            )
            client = subprocess.Popen([sys.executable, "-c", code], cwd=REPO_ROOT)
            deadline = time.monotonic() + 10
            while not received and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            deadline = time.monotonic() + 10
            while client.poll() is None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        self.assertEqual(received, [SHOW_MESSAGE])
        self.assertEqual(client.returncode, 0)


if __name__ == "__main__":
    unittest.main()
