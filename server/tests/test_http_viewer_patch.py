import hashlib
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("http_patch", Path(__file__).resolve().parents[1] / "ops/patch-http-viewer.py")
updater = importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)
sha = lambda value: hashlib.sha256(value).hexdigest()


class ViewerPatchTests(unittest.TestCase):
    def config(self):
        return {"work_directory": "/opt/wxnotify/app/http-viewer-20261028100749-15dcb449", "public_origin": "http://8.8.8.8:18083",
                "expires_at": int(time.time()) + 86400, "expected_old_sha256": sha(b"old = True\n"), "new_sha256": sha(b"new = True\n")}

    def test_scope_and_source_checked_before_writing(self):
        config = self.config();payload = b"new = True\n"
        updater.validate_config(config, payload)
        for change in ({"work_directory": "/opt/other"}, {"public_origin": "http://8.8.8.8:80"}, {"expires_at": 1}, {"new_sha256": "0" * 64}):
            with self.assertRaises(ValueError):
                updater.validate_config({**config, **change}, payload)

    def scenario(self, fail=False, changed=False):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp);work = home / "work";work.mkdir()
            target = work / "temporary-http-viewer.py";old = b"old = True\n";new = b"new = True\n";target.write_bytes(old if not changed else b"other = True\n")
            units = home / ".config/systemd/user";units.mkdir(parents=True)
            config = self.config()
            unit = "WorkingDirectory=" + str(work) + "\nExecStart=python --origin " + config["public_origin"] + " --expires-at " + str(config["expires_at"]) + " --temporary-http-confirmed\n"
            unit_path = units / updater.UNIT;unit_path.write_text(unit)
            calls = [];starts = []
            def run(args):
                calls.append(args)
                if "show" in args:
                    return str(work)
                if "start" in args:
                    starts.append(1)
                    if fail and len(starts) == 1:
                        raise ValueError("fixture startup failure")
                return ""
            def probe(authority, method="GET", route="/viewer/health"):
                if method == "POST":
                    return 401, {"error": "unauthorized"}
                return 200, {"backend_ready": True, "expires_at": config["expires_at"], "gateway_sha256": sha(target.read_bytes())}
            with patch.object(updater, "validate_config", return_value=work), patch.object(updater, "private"), patch.object(updater.Path, "home", return_value=home), patch.object(updater, "run", side_effect=run), patch.object(updater, "probe", side_effect=probe):
                if fail or changed:
                    with self.assertRaises(ValueError):
                        updater.install(config, new)
                else:
                    result = updater.install(config, new)
                    self.assertTrue(result["logout_without_json_reaches_backend"])
                    self.assertFalse(result["teacher_credentials_used"])
                    before = len(calls)
                    updater.install(config, new)
                    self.assertFalse(any("stop" in item or "start" in item for item in calls[before:]))
            self.assertEqual(unit_path.read_text(), unit)
            self.assertTrue(all(item[3] == updater.UNIT for item in calls if "stop" in item or "start" in item))
            self.assertEqual(target.read_bytes(), b"other = True\n" if changed else old if fail else new)
            if not changed:
                self.assertEqual((work / "temporary-http-viewer.before-logout-fix.py").read_bytes(), old)
            else:
                self.assertFalse((work / "temporary-http-viewer.before-logout-fix.py").exists())

    def test_atomic_patch_preserves_unit_and_deadline_and_allows_idempotent_check(self):
        self.scenario()

    def test_start_failure_restores_only_viewer_code(self):
        self.scenario(fail=True)

    def test_unexpected_current_code_is_not_overwritten(self):
        self.scenario(changed=True)

    def recover_scenario(self, already=False, unexpected=False, failed=False):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp);work = home / "work";work.mkdir()
            target = work / "temporary-http-viewer.py";old = b"old = True\n";target.write_bytes(b"other = True\n" if unexpected else old)
            config = self.config();units = home / ".config/systemd/user";units.mkdir(parents=True)
            unit = "WorkingDirectory=" + str(work) + "\nExecStart=python --origin " + config["public_origin"] + " --expires-at " + str(config["expires_at"]) + " --temporary-http-confirmed\n"
            unit_file = units / updater.UNIT;unit_file.write_text(unit)
            calls = []
            def run(args):
                calls.append(args)
                if failed and "start" in args:
                    raise ValueError("fixture start failure")
                return "fictional startup failure" if args[0] == "journalctl" else "ActiveState=active"
            ready = (200, {"backend_ready": True, "gateway_sha256": sha(old), "expires_at": config["expires_at"]})
            with patch.object(updater, "validate_config", return_value=work), patch.object(updater, "private"), patch.object(updater.Path, "home", return_value=home), patch.object(updater, "run", side_effect=run), patch.object(updater.time, "sleep"), patch.object(updater, "probe", side_effect=OSError("fixture disconnected") if failed else [ready if already else OSError("fixture disconnected"), ready]):
                if unexpected:
                    with self.assertRaises(ValueError):
                        updater.recover_original(config, b"new = True\n")
                    self.assertEqual(calls, [])
                else:
                    result = updater.recover_original(config, b"new = True\n")
                    self.assertEqual(result["original_viewer_ready"], not failed)
                    self.assertEqual(result["start_operation_failed"], failed)
                    self.assertEqual(result["already_healthy"], already)
                    self.assertFalse(result["source_changed"])
                    self.assertEqual(result["expires_at"], config["expires_at"])
                    self.assertEqual(result["viewer_journal_tail"], "fictional startup failure")
                    self.assertEqual(any("start" in item for item in calls), not already)
                    self.assertTrue(all(item[-1] == updater.UNIT for item in calls if "start" in item or "reset-failed" in item))
            self.assertEqual(target.read_bytes(), b"other = True\n" if unexpected else old)
            self.assertEqual(unit_file.read_text(), unit)
            self.assertEqual(list(work.iterdir()), [target])

    def test_recover_original_code_only_restarts_the_failed_viewer(self):
        self.recover_scenario()

    def test_healthy_original_viewer_is_not_restarted(self):
        self.recover_scenario(already=True)

    def test_recovery_refuses_unknown_code_without_service_changes(self):
        self.recover_scenario(unexpected=True)

    def test_failed_recovery_still_returns_logs_and_does_not_claim_ready(self):
        self.recover_scenario(failed=True)


if __name__ == "__main__":
    unittest.main()
