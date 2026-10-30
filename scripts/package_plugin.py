#!/usr/bin/env python3
"""Build the QGIS plugin zip (``dist/AGSGeoModel-<version>.zip``) and optionally upload it.

The zip contains the ``agsgeomodel`` package (the plugin folder) with LICENSE and the rendered
help, without tests, caches or development files, as required by plugins.qgis.org. The script
checks ``metadata.txt`` against the package version and the repository's required fields.

    python scripts/package_plugin.py --out dist            # build
    python scripts/package_plugin.py --out dist --upload   # build + upload (OSGEO_USERNAME / OSGEO_PASSWORD)
"""

from __future__ import annotations

import argparse
import configparser
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "agsgeomodel"
REQUIRED = ("name", "qgisMinimumVersion", "description", "version", "author", "email", "about", "tracker", "repository")
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".orig", ".rej", ".swp"}


def package_version() -> str:
    text = (PKG / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def check_metadata() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(PKG / "metadata.txt", encoding="utf-8")
    meta = cp["general"]
    missing = [k for k in REQUIRED if not meta.get(k)]
    if missing:
        sys.exit(f"metadata.txt is missing: {', '.join(missing)}")
    if meta["version"] != package_version():
        sys.exit(f"metadata.txt version {meta['version']} != agsgeomodel.__version__ {package_version()}")
    if not (PKG / meta.get("icon", "")).exists():
        sys.exit(f"icon {meta.get('icon')} not found")
    if "@example.org" in meta["email"]:
        print("warning: metadata.txt still has the placeholder maintainer email", file=sys.stderr)
    return cp


def render_help() -> None:
    """docs/user_guide.md -> agsgeomodel/help/index.html (Python-Markdown if installed, else <pre>)."""
    src = ROOT / "docs" / "user_guide.md"
    if not src.exists():
        return
    out = PKG / "help"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    md = src.read_text(encoding="utf-8")
    # images used by the guide travel with the plugin; links to other pages go to the online docs
    for rel in sorted(set(re.findall(r"\]\((images/[^)\s]+)\)", md))):
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "docs" / rel, out / rel)
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(PKG / "metadata.txt", encoding="utf-8")
    docs_url = cp["general"].get("repository", "").rstrip("/") + "/blob/main/docs/"
    md = re.sub(
        r"\]\((?!https?:)([^)#\s]+\.md)(#[^)\s]*)?\)", lambda m: f"]({docs_url}{m.group(1)}{m.group(2) or ''})", md
    )
    try:
        import markdown

        body = markdown.markdown(md, extensions=["tables", "fenced_code", "toc"])
    except ImportError:
        import html

        body = f"<pre>{html.escape(md)}</pre>"
    (out / "index.html").write_text(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>AGSGeoModel help</title>"
        "<style>body{font-family:sans-serif;max-width:960px;margin:2em auto;padding:0 1em;line-height:1.5}"
        "code,pre{background:#f4f6f7}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:3px 6px}"
        "img{max-width:100%;height:auto}"
        "</style></head><body>" + body + "</body></html>",
        encoding="utf-8",
    )


def build(out_dir: Path) -> Path:
    check_metadata()
    render_help()
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / f"AGSGeoModel-{package_version()}.zip"
    n = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PKG.rglob("*")):
            rel = path.relative_to(PKG)
            if path.is_dir() or set(rel.parts) & EXCLUDE_DIRS or path.suffix in EXCLUDE_SUFFIX:
                continue
            zf.write(path, f"agsgeomodel/{rel.as_posix()}")
            n += 1
        zf.write(ROOT / "LICENSE", "agsgeomodel/LICENSE")
        zf.write(ROOT / "README.md", "agsgeomodel/README.md")
    print(f"{zpath} ({n + 2} files, {zpath.stat().st_size / 1e6:.1f} MB)")
    return zpath


def upload(zpath: Path) -> None:
    import xmlrpc.client

    user, password = os.environ.get("OSGEO_USERNAME"), os.environ.get("OSGEO_PASSWORD")
    if not user or not password:
        sys.exit("set OSGEO_USERNAME and OSGEO_PASSWORD to upload")
    url = f"https://{user}:{password}@plugins.qgis.org:443/plugins/RPC2/"
    server = xmlrpc.client.ServerProxy(url, verbose=False)
    plugin_id, version_id = server.plugin.upload(xmlrpc.client.Binary(zpath.read_bytes()))
    print(f"uploaded: plugin {plugin_id}, version {version_id}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="dist")
    ap.add_argument("--upload", action="store_true")
    a = ap.parse_args()
    z = build(ROOT / a.out)
    if a.upload:
        upload(z)
    return 0


if __name__ == "__main__":
    sys.exit(main())
