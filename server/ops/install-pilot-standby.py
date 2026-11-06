"""New standby unit only; never modify the source/candidate databases or existing services."""
import base64
from contextlib import closing
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

UNIT = "wxnotify-single-class-standby.service"
RELEASE = "/opt/wxnotify/app/roster-review-v0.5.0-fb7c43a1-20261027065406-ff172ce2/release"
PYTHON = "/opt/wxnotify/app/business-v0.4.0-c59dab1f/.venv/bin/python"


def stage_path(value):
    if not re.fullmatch(r"/opt/wxnotify/app/single-class-offline-\d{14}-[a-f0-9]{8}", value):
        raise ValueError("Existing confirmed offline stage required")
    return Path(value)


def verify_runtime(payload):
    runtime = {"__name__": "standby_runtime_check"}
    exec(compile(payload, "<standby-runtime>", "exec"), runtime)
    runtime["verify_release"](Path(RELEASE))


def run(args, allowed=(0,)):
    p = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if p.returncode not in allowed:
        raise ValueError("Standby user-service operation failed")
    return p.stdout.strip()


def health(port):
    conn = HTTPConnection("127.0.0.1", port, timeout=4)
    try:
        conn.request("GET", "/health/ready")
        r = conn.getresponse();raw = r.read(32769)
        if r.status != 200 or len(raw) > 32768:
            raise ValueError("Standby readiness not confirmed")
        return json.loads(raw)
    finally:conn.close()


def unit(work):
    if not re.fullmatch(r"/opt/wxnotify/app/single-class-offline-\d{14}-[a-f0-9]{8}/standby", str(work)):
        raise ValueError("Fixed new standby directory required")
    return f"""[Unit]
Description=Future School isolated pilot standby (read only, sync disabled)
StartLimitIntervalSec=60
StartLimitBurst=3
[Service]
Type=exec
WorkingDirectory={work}
ExecStart={PYTHON} -u {work}/pilot-standby.py --config {work}/standby.private.json
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
KillMode=control-group
NoNewPrivileges=yes
UMask=0077
LimitCORE=0
LimitNOFILE=512
MemoryMax=256M
CPUQuota=50%
TasksMax=32
Environment=WXNOTIFY_ENABLE_WECHAT=0 WXNOTIFY_ENABLE_STUDENT_SYNC=0 WXNOTIFY_ENABLE_PARENT_BINDING=0 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1
UnsetEnvironment=PYTHONPATH PYTHONHOME
StandardOutput=null
StandardError=journal
SyslogIdentifier=wxnotify-single-class-standby
[Install]
WantedBy=default.target
"""


def modules(files):
    if set(files) != {"pilot-standby.py", "stage-single-class-pilot.py"}:
        raise ValueError("Unexpected standby programs")
    payloads = {}
    for name, item in files.items():
        raw = base64.b64decode(item["base64"], validate=True)
        if len(raw) > 131072 or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Standby source checksum differs")
        compile(raw, name, "exec");payloads[name] = raw
    helper = {"__name__": "standby_stage_helper"};exec(compile(payloads["stage-single-class-pilot.py"], "<stage-helper>", "exec"), helper)
    return payloads, helper


def install(config, files):
    payloads, helper = modules(files)
    stage = stage_path(config["stage_directory"])
    helper["private"](stage, True)
    receipt_file = stage / "stage-receipt.json";helper["private"](receipt_file)
    receipt = json.loads(receipt_file.read_bytes())
    if receipt != config["stage_receipt"] or receipt.get("stage_complete") is not True or receipt.get("service_started") is not False:
        raise ValueError("Actual offline stage receipt differs")
    plan_file = stage / "confirmed-plan.private.json";helper["private"](plan_file)
    plan = json.loads(plan_file.read_bytes())
    start, _ = helper["validate_plan"](plan, int(time.time()))
    for name, key in (("baseline-backup.sqlite", "baseline_backup_sha256"), ("pilot-offline.sqlite", "candidate_sha256")):
        file = stage / name;helper["private"](file)
        if helper["checksum"](file) != receipt[key]:
            raise ValueError("Offline baseline or candidate has changed; no overwrite")
    verify_runtime(payloads["pilot-standby.py"])
    for name in ("wxnotify-roster-review.service", "wxnotify-business-pilot.service", "wxnotify-http-viewer.service"):
        if run(["systemctl", "--user", "is-active", name]) != "active":
            raise ValueError("An existing service needs inspection first")
    old_health = health(18082)
    if old_health.get("roster_review_only") is not True or old_health.get("student_sync_enabled") is not False:
        raise ValueError("Existing read-only service changed")
    work = stage / "standby"
    units = Path.home() / ".config/systemd/user";helper["private"](units, True)
    target = units / UNIT
    if work.exists() or work.is_symlink() or target.exists() or target.is_symlink() or (units / (UNIT + ".d")).exists():
        raise ValueError("Standby already prepared; inspect existing state instead of rerunning")
    if run(["systemctl", "--user", "show", UNIT, "-p", "LoadState", "--value"], allowed=(0, 1)) != "not-found":
        raise ValueError("Standby unit is not new")
    with socket.socket() as probe:probe.bind(("127.0.0.1", 18084))
    content = unit(work)
    work.mkdir(mode=0o700)
    helper["snapshot"](stage / "pilot-offline.sqlite", work / "business.sqlite")
    with closing(helper["connect"](work / "business.sqlite")) as db:
        helper["compare"](db, plan, start)
        if helper["preserved_digest"](db) != receipt["preserved_data_sha256"]:
            raise ValueError("Working database business baseline differs")
    cfg = {"format": "future-school-pilot-standby-config", "release": RELEASE, "plan": plan, "stage_receipt": receipt,
           "helper_sha256": files["stage-single-class-pilot.py"]["sha256"], "runtime_sha256": files["pilot-standby.py"]["sha256"]}
    for name, raw in {**payloads, "standby.private.json": json.dumps(cfg, ensure_ascii=True, indent=2).encode()}.items():
        with os.fdopen(os.open(work / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:stream.write(raw)
    written = False
    try:
        with os.fdopen(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as stream:stream.write(content)
        written = True
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "enable", "--now", UNIT])
        ready = None
        for _ in range(30):
            try:
                state = health(18084)
                if state.get("mode") == "pilot_standby" and state.get("standby_code_sha256") == cfg["runtime_sha256"] and state.get("selected_students") == receipt["selected_students"] and all(state.get(k) is False for k in ("business_ready", "student_sync_enabled", "wechat_sending_enabled", "device_registration_enabled", "single_class_write_enabled", "parent_binding_enabled")) and state.get("roster_review_only") is True:
                    ready = state;break
            except (OSError, ValueError):pass
            time.sleep(.5)
        if ready is None:raise ValueError("New standby readiness failed")
        for name, key in (("baseline-backup.sqlite", "baseline_backup_sha256"), ("pilot-offline.sqlite", "candidate_sha256")):
            if helper["checksum"](stage / name) != receipt[key]:raise ValueError("Original offline snapshot changed")
        for name in ("wxnotify-roster-review.service", "wxnotify-business-pilot.service", "wxnotify-http-viewer.service"):
            if run(["systemctl", "--user", "is-active", name]) != "active":raise ValueError("Existing service state changed")
        result = {"format": "future-school-pilot-standby-installed", "schema_version": 1, "unit": UNIT,
                  "stage_directory": str(stage), "runtime_sha256": cfg["runtime_sha256"], "port": 18084, "loopback_only": True,
                  "standby_ready": True, "selected_students": receipt["selected_students"], "source_candidate_unchanged": True,
                  "service_state": run(["systemctl", "--user", "show", UNIT, "--no-pager", "-p", "ActiveState", "-p", "SubState", "-p", "UnitFileState", "-p", "NRestarts"]),
                  "existing_services_modified": False, "business_writes_enabled": False, "lesson_created": False,
                  "device_enabled": False, "wechat_enabled": False}
        with os.fdopen(os.open(work / "install-receipt.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as stream:json.dump(result, stream, indent=2)
        return result
    except BaseException:
        if written:run(["systemctl", "--user", "disable", "--now", UNIT])
        raise


def pause(config):
    stage = stage_path(config["stage_directory"])
    if run(["systemctl", "--user", "show", UNIT, "-p", "WorkingDirectory", "--value"]) != str(stage / "standby"):
        raise ValueError("Pause targets only the confirmed standby")
    run(["systemctl", "--user", "disable", "--now", UNIT])
    state = run(["systemctl", "--user", "show", UNIT, "-p", "ActiveState", "-p", "UnitFileState"])
    if "ActiveState=inactive" not in state or "UnitFileState=disabled" not in state:raise ValueError("Standby pause was not confirmed")
    return {"format": "future-school-pilot-standby-paused", "schema_version": 1, "unit": UNIT, "stage_directory": str(stage), "service_state": state, "existing_services_modified": False}
