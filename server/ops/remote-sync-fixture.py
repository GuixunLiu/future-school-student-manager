"""Foreground SSH-supervised fictional sync fixture. EOF, disconnect or deadline stops only its child."""

import base64
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time

MANIFEST = "df5e8eafce1f220d2252b1768db50a8a869bf2a2b2e563cd60a6312e42f10359"


def validate(config):
    import re
    release = Path(config["release"])
    work = Path(config["work"])
    if (not re.fullmatch(r"/opt/wxnotify/app/roster-review-v0\.5\.0-[a-f0-9]{8}-[0-9]{14}-[a-f0-9]{8}/release", config["release"])
            or not re.fullmatch(r"/opt/wxnotify/app/sync-validation-[0-9]{14}-[a-f0-9]{8}", config["work"])
            or type(config["port"]) is not int or config["port"] != 18183):
        raise ValueError("Only the explicit new synthetic fixture is supported")
    if any(p.is_symlink() for p in (release, work, *release.parents, *work.parents)):
        raise ValueError("Linked paths are not supported")
    if [f.get("name") for f in config["files"]] != ["serve_sync_pilot_fixture.py", "serve_tablet_fixture.py"]:
        raise ValueError("Unexpected fixture files")
    for f in config["files"]:
        payload = base64.b64decode(f["base64"], validate=True)
        if len(payload) > 65536 or hashlib.sha256(payload).hexdigest() != f["sha256"]:
            raise ValueError("Fixture code checksum differs")
    return release, work


def monitor(child, descriptor, *, now=time.monotonic, wait=select.select, read=os.read):
    deadline = now() + 7200
    while child.poll() is None and now() < deadline:
        ready, _, _ = wait([descriptor], [], [], 1)
        if ready and not read(descriptor, 1024):
            break
    if child.poll() not in (None, 0):
        raise ValueError("Synthetic child did not finish successfully")


def close_child(child):
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def main():
    os.umask(0o077)
    child = None
    try:
        data = sys.stdin.buffer.readline(262145)
        if len(data) > 262144:
            raise ValueError("Fixture configuration too large")
        config = json.loads(base64.b64decode(data.strip(), validate=True))
        release, work = validate(config)
        raw = (release / "MANIFEST.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != MANIFEST:
            raise ValueError("Reviewed runtime manifest differs")
        manifest = json.loads(raw)
        if len(manifest["files"]) != 27:
            raise ValueError("Unexpected runtime file list")
        for name, expected in manifest["files"].items():
            if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:
                raise ValueError("Unsafe runtime source path")
            source = release / name
            if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise ValueError("Reviewed runtime source differs")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", config["port"]))
        work.mkdir(mode=0o700, exist_ok=False)
        for f in config["files"]:
            fd = os.open(work / f["name"], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(base64.b64decode(f["base64"], validate=True))
        runtime = Path("/opt/wxnotify/app/business-v0.4.0-c59dab1f/.venv/bin/python")
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONHOME", "PYTHONPATH"}}
        env.update(PYTHONPATH=str(release), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
                   FUTURE_SCHOOL_FIXTURE_RUNTIME=str(release),
                   WXNOTIFY_ENABLE_WECHAT="0", WXNOTIFY_ENABLE_STUDENT_SYNC="0", WXNOTIFY_ENABLE_PARENT_BINDING="0")
        def stop(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGHUP, stop)
        child = subprocess.Popen([str(runtime), "-u", str(work / "serve_sync_pilot_fixture.py"), "--database", str(work / "synthetic.sqlite"),
                                  "--port", str(config["port"]), "--clock-start", str(int(time.time()))],
                                 cwd=work, env=env, stdin=subprocess.DEVNULL)
        print("SYNC_FIXTURE_STARTED", flush=True)
        monitor(child, sys.stdin.fileno())
    except KeyboardInterrupt:
        pass
    except Exception:
        print("STOP: remote synthetic fixture not ready; existing business services were not changed.", file=sys.stderr, flush=True)
        return 2
    finally:
        close_child(child)
        if child is not None:
            print("SYNC_FIXTURE_CLOSED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
