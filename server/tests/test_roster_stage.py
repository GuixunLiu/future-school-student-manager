"""Package and service boundary checks; no SSH or systemd commands are executed."""

import configparser
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


stage = module("roster_stage", ROOT / "ops/stage-roster-review.py")
packager = module("roster_packager", ROOT / "package-roster.py")


class StageTests(unittest.TestCase):
    def test_unit_is_separate_loopback_review_with_old_environment_retained(self):
        unit = stage.build_unit(Path("/opt/wxnotify/app/new/release"), Path("/opt/wxnotify/app/new/data/roster.sqlite"))
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(unit)
        service = config["Service"]
        self.assertEqual(service["WorkingDirectory"], "/opt/wxnotify/app/new/release")
        self.assertIn("business_next.cli serve", service["ExecStart"])
        self.assertIn("--review-only --port 18082", service["ExecStart"])
        self.assertNotIn("18081", unit)
        self.assertNotIn("0.0.0.0", unit)
        self.assertEqual(service["UMask"], "0077")
        self.assertEqual(service["MemoryMax"], "256M")
        self.assertNotEqual(stage.UNIT, stage.OLD_UNIT)
        with self.assertRaises(ValueError):
            stage.build_unit(Path("/tmp/unsafe path"), Path("/tmp/data"))

    def test_package_pins_unchanged_base_and_rejects_unexpected_members(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            output = base / "packaged"
            receipt = packager.package(output)
            archive = Path(receipt["path"])
            old = base / "old-release"
            old.mkdir()
            frozen = ROOT / "../../../outputs/server-business-v0.4.0/wxnotify-business-v0.4.0-local-test.zip"
            with zipfile.ZipFile(frozen) as z:
                for name in list(stage.BASE_FILES) + ["MANIFEST.json"]:
                    target = old / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(name))
            with patch.object(stage, "OLD_RELEASE", old):
                payloads = stage.validate_package(archive, receipt["sha256"], receipt["manifest_sha256"])
                self.assertEqual(len(payloads), 28)
                self.assertNotIn("pilot.sqlite", payloads)
                with self.assertRaises(ValueError):
                    stage.validate_package(archive, "0" * 64, receipt["manifest_sha256"])
                with zipfile.ZipFile(archive, "a") as z:
                    z.writestr("../../unexpected.py", "must reject")
                with self.assertRaisesRegex(ValueError, "members"):
                    stage.validate_package(archive, hashlib.sha256(archive.read_bytes()).hexdigest(), receipt["manifest_sha256"])


if __name__ == "__main__":
    unittest.main()
