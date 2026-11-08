"""Publish local-only pinned synthetic integration launcher; no network or device operations."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


def main():
    root = Path(__file__).resolve().parents[2]
    outputs = root.parents[1] / "outputs"
    destination = outputs / "remote-sync-validation-2026-10-28-ready"
    destination.mkdir(exist_ok=True)
    package = outputs / "server-business-v0.5.0-ready/wxnotify-business-v0.5.0-roster-review.zip"
    with zipfile.ZipFile(package) as archive:
        raw = archive.read("MANIFEST.json")
        assert hashlib.sha256(raw).hexdigest() == "df5e8eafce1f220d2252b1768db50a8a869bf2a2b2e563cd60a6312e42f10359"
        files = json.loads(raw)["files"]
        assert len(files) == 27
        for name, expected in files.items():
            assert hashlib.sha256((root / "server" / name).read_bytes()).hexdigest() == expected, name
    for name in ("run-remote-sync-check.cjs", "start-remote-sync-fixture.cjs"):
        shutil.copyfile(root / "server/ops" / name, destination / name)
    launcher = (root / "server/ops/run-remote-sync-check.cmd").read_text(encoding="ascii")
    (destination / "RUN-REMOTE-SYNC-CHECK.cmd").write_bytes(launcher.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii"))
    paths = {
        "supervisor": root / "server/ops/remote-sync-fixture.py",
        "fixture": root / "server/tests/serve_sync_pilot_fixture.py",
        "receipt_loss": root / "server/tests/serve_tablet_fixture.py",
        "driver": root / "device-sync-tablet-test.cjs",
        "starter": root / "server/ops/start-remote-sync-fixture.cjs",
        "apk": root / "app/build/outputs/apk/debug/app-debug.apk",
    }
    assert hashlib.sha256(paths["apk"].read_bytes()).hexdigest() == "20e6097c6aa2d72d422f063bd755ec00370390351b6463e4a9415877ad64095a"
    config = dict(host="47.97.226.219", user="wxnotify-deploy", port=22, remote_port=18183,
                  release="/opt/wxnotify/app/roster-review-v0.5.0-fb7c43a1-20261027065406-ff172ce2/release",
                  serial="HVA13XVR", repository=str(root),
                  temp_directory=str(outputs / "tablet-review-test-temp"))
    for key, file in paths.items():
        config[key] = dict(path=str(file), sha256=hashlib.sha256(file.read_bytes()).hexdigest())
    (destination / "remote-sync-config.private.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    (destination / "使用说明.md").write_text("""# 平板与云服务器独立同步联调

保持原平板USB连接、已解锁且屏幕亮着。双击 RUN-REMOTE-SYNC-CHECK.cmd，出现 SSH password 提示时输入服务器密码。密码不显示字符是正常现象，不会记录到文件。

工具临时安装“未来学校同步测试（独立）”，使用虚构同名学生、单独测试数据库和127.0.0.1:18183临时服务，通过SSH转发验证云端同步。约几分钟，保持窗口和数据线连接。无需操作测试App，也不需要在日常App签到。

成功时显示“远程同步联调全部通过”；临时服务停止，独立测试App移除，报告保存在 outputs/sync-pilot-2026-10-27。测试不启用真实资料同步、家长绑定或微信发送，不改原服务或日常App。服务器会保留独立的虚构测试库作为核对记录。

若失败，请保留窗口结果交给Codex核对。若关闭窗口或连接断开，服务器测试服务随SSH输入结束停止，并有最长两小时退出期限。此前本机隔离实测已通过；本入口尚待服务器实际执行，不能据此称远程已通过。
""", encoding="utf-8")
    print(json.dumps({"prepared": True, "checked_runtime_files": len(files), "pinned_inputs": len(paths),
                      "network_connected": False, "launcher": str(destination / "RUN-REMOTE-SYNC-CHECK.cmd")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
