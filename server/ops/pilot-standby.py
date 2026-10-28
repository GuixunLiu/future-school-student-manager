"""Separate loopback, READ-ONLY standby. Activation and real device transport are not implemented here."""
import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

RELEASE = Path("/opt/wxnotify/app/roster-review-v0.5.0-fb7c43a1-20261027065406-ff172ce2/release")
MANIFEST = "df5e8eafce1f220d2252b1768db50a8a869bf2a2b2e563cd60a6312e42f10359"


def verify_release(release):
    raw = (release / "MANIFEST.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST:
        raise ValueError("Frozen release manifest differs")
    files = json.loads(raw)["files"]
    if len(files) != 27:
        raise ValueError("Frozen runtime file count differs")
    for name, expected in files.items():
        path = release / name
        if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name or any(p.is_symlink() for p in (path, *path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen runtime source differs")


def build_app(database, plan, receipt, helper, *, clock=None, allow_test_client=False):
    from datetime import datetime
    from fastapi.responses import Response
    from business_next.api import create_app
    helper.private(database)
    start = datetime.fromisoformat(plan["starts_at"])

    def verify_standby():
        with closing(helper.connect(database)) as db:
            helper.verify(db)
            summary = helper.compare(db, plan, start)
            if helper.preserved_digest(db) != receipt["preserved_data_sha256"] or summary["selected_students"] != receipt["selected_students"] or summary["selected_remaining_units"] != receipt["selected_remaining_units"]:
                raise ValueError("Standby business baseline differs")
            checks = ("SELECT count(*) FROM devices WHERE active=1", "SELECT count(*) FROM device_classes", "SELECT count(*) FROM test_parent_links", "SELECT count(*) FROM notification_preferences WHERE enabled<>0", "SELECT count(*) FROM notification_schedule WHERE enabled<>0", "SELECT count(*) FROM outbox WHERE status='pending_simulation'")
            if any(db.execute(sql).fetchone()[0] for sql in checks):
                raise ValueError("Standby device or notification state was enabled")
        return summary

    verify_standby()
    api = create_app(database, clock, allow_test_client=allow_test_client, review_only=True)
    original_ready = next(r.endpoint for r in api.router.routes if r.path == "/health/ready")
    original_home = next(r.endpoint for r in api.router.routes if r.path == "/")
    api.router.routes[:] = [r for r in api.router.routes if r.path not in {"/health/ready", "/"}]

    @api.get("/health/ready")
    def ready():
        summary = verify_standby()
        return original_ready() | {"mode": "pilot_standby", "roster_review_only": True, "pilot_phase": "standby_read_only",
                                   "standby_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                   "selected_students": summary["selected_students"], "business_ready": False,
                                   "device_registration_enabled": False, "single_class_write_enabled": False,
                                   "student_sync_enabled": False, "wechat_sending_enabled": False, "parent_binding_enabled": False}

    @api.get("/", include_in_schema=False)
    def home():
        text = original_home().body.decode("utf-8").replace("名册核对副本 · 余额以平板最新记录为准 · 微信尚未启用", "单班试运行待启用 · 资料核对 · 平板同步及微信未开启")
        return Response(text, media_type="text/html")
    return api


def main():
    parser = argparse.ArgumentParser(description=__doc__);parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_bytes());work = args.config.parent
    if args.config.name != "standby.private.json" or config["format"] != "future-school-pilot-standby-config" or config["release"] != str(RELEASE):
        raise ValueError("Explicit standby configuration required")
    verify_release(RELEASE)
    helper_path = work / "stage-single-class-pilot.py"
    if hashlib.sha256(helper_path.read_bytes()).hexdigest() != config["helper_sha256"] or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != config["runtime_sha256"]:
        raise ValueError("Standby tools differ")
    spec = importlib.util.spec_from_file_location("standby_helper", helper_path);helper = importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    helper.private(work, True);helper.private(args.config);helper.private(helper_path);helper.private(Path(__file__))
    sys.path.insert(0, str(RELEASE))
    import uvicorn
    api = build_app(work / "business.sqlite", config["plan"], config["stage_receipt"], helper)
    uvicorn.run(api, host="127.0.0.1", port=18084, access_log=False, proxy_headers=False, server_header=False,
                ws="none", limit_concurrency=16, timeout_keep_alive=5, timeout_graceful_shutdown=10)


if __name__ == "__main__":main()
