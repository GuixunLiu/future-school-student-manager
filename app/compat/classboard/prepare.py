"""Generate the release asset overrides for a Chrome 51-era Android WebView."""

import argparse
from pathlib import Path
import re
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("esbuild", help="Path to the esbuild executable")
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    app = folder.parents[1]
    assets = app / "src/main/assets"
    tablet = app.parent / "server/management/tablet"
    output = app / "build/generated/classboard-assets"
    output.mkdir(parents=True, exist_ok=True)
    index = output / "index.html"
    index.unlink(missing_ok=True)
    page = (assets / "index.html").read_text(encoding="utf-8")
    for name in re.findall(r'<script src="([^"]+\.js)"', page):
        if name.endswith(".min.js"):  # Prebuilt vendor libraries have their own build target.
            continue
        source = assets / name
        if not source.is_file():
            source = tablet / name
        subprocess.run(
            [args.esbuild, str(source), "--target=chrome51", "--charset=utf8", "--log-level=error",
             f"--outfile={output / name}"],
            check=True,
        )
    for name in ("classboard-compat.js", "classboard-compat.css"):
        shutil.copyfile(folder / name, output / name)
    page = page.replace("</head>", '  <link rel="stylesheet" href="classboard-compat.css">\n</head>')
    page = page.replace(
        '<script src="class-space.js">',
        '<script src="classboard-compat.js"></script>\n  <script src="class-space.js">',
        1,
    )
    index.write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
