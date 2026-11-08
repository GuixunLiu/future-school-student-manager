import errno
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

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

    def scenario(self, fail=False, changed=False, health_failure=False, rollback_failure=False, existing_backup=False, preflight_failure=False):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp);work = home / "work";work.mkdir()
            target = work / "temporary-http-viewer.py";old = b"old = True\n";new = b"new = True\n";target.write_bytes(old if not changed else b"other = True\n")
            if existing_backup:
                (work / "temporary-http-viewer.before-logout-fix.py").write_bytes(old)
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
            readiness = [False, not rollback_failure] if health_failure else [not rollback_failure] if fail else [True]
            with patch.object(updater, "validate_config", return_value=work), patch.object(updater, "private"), patch.object(updater.Path, "home", return_value=home), patch.object(updater, "run", side_effect=run), patch.object(updater, "probe", side_effect=probe), patch.object(updater, "restart_smoke", side_effect=ValueError("fixture preflight") if preflight_failure else None, return_value={"platform": "fixture", "rounds": 2, "real_backend_accessed": False}), patch.object(updater, "wait_bindable") as port_wait, patch.object(updater, "ready_matches", side_effect=readiness) as checked_ready:
                if changed or preflight_failure:
                    with self.assertRaises(ValueError):
                        updater.install(config, new)
                    self.assertFalse(any("stop" in item for item in calls))
                elif fail or health_failure:
                    result = updater.install(config, new)
                    self.assertEqual(result["format"], "future-school-http-viewer-update-failed")
                    self.assertEqual(result["original_viewer_restored"], not rollback_failure)
                    self.assertEqual(result["active_source_sha256"], sha(old))
                    self.assertEqual(checked_ready.call_args.args[1], sha(old))
                    self.assertEqual([item.args[0] for item in port_wait.call_args_list], [True, False])
                else:
                    result = updater.install(config, new)
                    self.assertTrue(result["logout_without_json_reaches_backend"])
                    self.assertFalse(result["teacher_credentials_used"])
                    before = len(calls)
                    updater.install(config, new)
                    self.assertFalse(any("stop" in item or "start" in item for item in calls[before:]))
            self.assertEqual(unit_path.read_text(), unit)
            self.assertTrue(all(item[3] == updater.UNIT for item in calls if "stop" in item or "start" in item))
            self.assertEqual(target.read_bytes(), b"other = True\n" if changed else old if fail or health_failure or preflight_failure else new)
            if not changed and not preflight_failure:
                self.assertEqual((work / "temporary-http-viewer.before-logout-fix.py").read_bytes(), old)
            else:
                self.assertFalse((work / "temporary-http-viewer.before-logout-fix.py").exists())

    def test_atomic_patch_preserves_unit_and_deadline_and_allows_idempotent_check(self):
        self.scenario()

    def test_start_failure_restores_only_viewer_code(self):
        self.scenario(fail=True)

    def test_unexpected_current_code_is_not_overwritten(self):
        self.scenario(changed=True)

    def test_reuses_only_verified_original_backup_after_reviewed_recovery(self):
        self.scenario(existing_backup=True)

    def test_health_failure_rolls_back_and_checks_original_readiness(self):
        self.scenario(health_failure=True)

    def test_failed_rollback_health_is_not_reported_as_recovered(self):
        self.scenario(health_failure=True, rollback_failure=True)

    def test_failed_isolated_preflight_never_stops_public_viewer(self):
        self.scenario(preflight_failure=True)

    def test_port_wait_retries_address_in_use_without_shared_listener(self):
        factory = MagicMock();sock = factory.return_value.__enter__.return_value
        sock.bind.side_effect = [OSError(errno.EADDRINUSE, "fictional busy"), None]
        with patch.object(updater.socket, "socket", factory), patch.object(updater.time, "monotonic", side_effect=[0, 1]), patch.object(updater.time, "sleep") as sleep:
            updater.wait_bindable(True)
        self.assertEqual(sock.bind.call_count, 2)
        sleep.assert_called_once_with(1)
        self.assertEqual(sock.setsockopt.call_count, 2)
        for call in sock.setsockopt.call_args_list:
            self.assertEqual(call.args, (updater.socket.SOL_SOCKET, updater.socket.SO_REUSEADDR, 1))

    def test_original_nonreuse_port_wait_is_bounded(self):
        factory = MagicMock();sock = factory.return_value.__enter__.return_value
        sock.bind.side_effect = OSError(errno.EADDRINUSE, "fictional busy")
        with patch.object(updater.socket, "socket", factory), patch.object(updater.time, "monotonic", side_effect=[0, 91]), patch.object(updater.time, "sleep") as sleep:
            with self.assertRaises(ValueError):
                updater.wait_bindable(False)
        sock.setsockopt.assert_not_called();sleep.assert_not_called()

    def test_wrong_source_health_never_passes_readiness(self):
        with patch.object(updater, "probe", return_value=(200, {"gateway_sha256": "wrong", "backend_ready": True, "expires_at": 2000000000})), patch.object(updater.time, "monotonic", side_effect=[0, 21]):
            self.assertFalse(updater.ready_matches("fictional", "expected", 2000000000))

    def test_real_os_restart_preflight_uses_no_backend(self):
        source = Path(__file__).resolve().parents[1] / "ops/temporary-http-viewer.py"
        result = updater.restart_smoke(source.read_bytes(), source)
        self.assertEqual(result["rounds"], 2)
        self.assertFalse(result["real_backend_accessed"])

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
