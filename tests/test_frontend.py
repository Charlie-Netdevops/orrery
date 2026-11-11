"""Front-end checks: JS syntax, CSP compatibility (no inline code), no CDNs, no old branding."""
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
OWN_JS = ["app.js", "login.js"]
HTML = ["index.html", "login.html"]


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
@pytest.mark.parametrize("name", OWN_JS + ["vendor/marked.min.js", "vendor/purify.min.js"])
def test_js_syntax(name):
    r = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("name", HTML)
def test_html_is_csp_clean(name):
    html = (STATIC / name).read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script> would be blocked by the CSP"
    assert "<style" not in html, "inline <style> would be blocked by the CSP"
    assert not re.search(r"\sstyle=", html), "style= attributes would be blocked by the CSP"
    assert not re.search(r"\son[a-z]+=", html), "inline event handlers would be blocked by the CSP"


def test_no_cdn_or_remote_assets():
    for f in list(STATIC.glob("*.html")) + list(STATIC.glob("*.css")) + [STATIC / n for n in OWN_JS]:
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"""(src|href)=["']https?://""", text), f.name
        assert "cdn." not in text and "@import" not in text, f.name


def test_vendor_licenses_present():
    v = STATIC / "vendor"
    assert (v / "marked.min.js").exists() and (v / "marked.LICENSE.md").exists()
    assert (v / "purify.min.js").exists() and (v / "dompurify.LICENSE").exists()


def test_js_does_not_build_html_with_style_attributes():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'style="' not in js


def test_no_old_branding():
    old = bytes.fromhex("6e656f6e").decode()          # the prototype's name, kept out of the source on purpose
    pattern = re.compile(old + r"[ _-]?chat")
    for f in ROOT.rglob("*"):
        if f.is_file() and ".git" not in f.parts and f.suffix in {".py", ".js", ".html", ".css", ".md", ".yml", ".json", ".txt", ".example"}:
            assert not pattern.search(f.read_text(encoding="utf-8", errors="replace").lower()), f
