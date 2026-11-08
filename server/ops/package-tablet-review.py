"""Build a self-contained offline reviewer. No input backups, networking or server changes."""

import argparse
import base64
import hashlib
from pathlib import Path


def package(output_dir):
    source = Path(__file__).parent / "tablet-review"
    core, ui, css = ((source / name).read_text(encoding="utf-8") for name in ("core.js", "ui.js", "style.css"))
    script = core + "\n" + ui
    digest = lambda value: base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
    csp = f"default-src 'none'; script-src 'sha256-{digest(script)}'; style-src 'sha256-{digest(css)}'; connect-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
    html = (source / "page.html").read_text(encoding="utf-8").replace("@@CSP@@", csp).replace("@@CSS@@", css).replace("@@CORE@@\n@@UI@@", script)
    if "@@" in html:
        raise ValueError("Unresolved template marker")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "未来学校-迁移前核对-v0.1.0.html"
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(html)
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_name("SHA256SUMS.txt").write_text(checksum + "  " + target.name + "\n", encoding="utf-8", newline="\n")
    return target, checksum


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    target, checksum = package(parser.parse_args().output_dir)
    print(target)
    print("SHA256: " + checksum)
