"""Supervisor boundary tests; never execute SSH, a service or a real database."""

import base64
import copy
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock

spec=importlib.util.spec_from_file_location("remote_sync",Path(__file__).resolve().parents[1]/"ops/remote-sync-fixture.py")
remote=importlib.util.module_from_spec(spec);spec.loader.exec_module(remote)


class RemoteSyncTests(unittest.TestCase):
    def config(self):
        body=b"# fictional only\n"
        return {"release":"/opt/wxnotify/app/roster-review-v0.5.0-aabbccdd-20261027065406-aabbccdd/release",
                "work":"/opt/wxnotify/app/sync-validation-20261027065406-aabbccdd","port":18183,
                "files":[{"name":name,"sha256":hashlib.sha256(body).hexdigest(),"base64":base64.b64encode(body).decode()} for name in ("serve_sync_pilot_fixture.py","serve_tablet_fixture.py")]}

    def test_private_new_path_fixed_loopback_port_and_checksum_only(self):
        remote.validate(self.config())
        for mutate in (lambda c:c.update(work="/opt/wxnotify/app/business-data-v0.4.0"),lambda c:c.update(port=18082),
                       lambda c:c["files"][0].update(name="../unsafe.py"),lambda c:c["files"][0].update(sha256="0"*64)):
            c=copy.deepcopy(self.config());mutate(c)
            with self.assertRaises(ValueError):remote.validate(c)

    def test_eof_ends_monitor_and_cleanup_terminates_only_child(self):
        child=Mock();child.poll.return_value=None
        remote.monitor(child,99,now=lambda:0,wait=lambda *args:([99],[],[]),read=lambda *args:b"")
        remote.close_child(child);child.terminate.assert_called_once();child.wait.assert_called_once_with(timeout=8);child.kill.assert_not_called()

    def test_deadline_and_child_failure(self):
        child=Mock();child.poll.return_value=None
        clock=iter((0,7201));remote.monitor(child,99,now=lambda:next(clock),wait=Mock(side_effect=AssertionError("should not wait after deadline")))
        child.poll.return_value=2
        with self.assertRaises(ValueError):remote.monitor(child,99)

    def test_unresponsive_child_is_killed_after_bounded_wait(self):
        child=Mock();child.poll.return_value=None;child.wait.side_effect=[subprocess.TimeoutExpired("fixture",8),0]
        remote.close_child(child);child.terminate.assert_called_once();child.kill.assert_called_once();self.assertEqual(child.wait.call_count,2)


if __name__=="__main__":unittest.main()
