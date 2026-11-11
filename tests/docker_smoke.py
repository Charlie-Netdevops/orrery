"""End-to-end smoke test in Docker, using the MOCK provider only (never real keys).

    python tests/docker_smoke.py            # builds orrery:smoketest, runs on 127.0.0.1:3200

Steps: build image -> verify no .env inside -> start mock provider + Orrery on a private
network -> health check -> security headers -> one streamed reply -> key-leak sweep of
responses and container logs -> LAN mode without password must refuse to start.
Everything it creates (containers, network, image, temp files) is removed afterwards.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = "orrery:smoketest"
NET = "orrery-smoketest-net"
MOCK = "orrery-smoketest-mock"
APP = "orrery-smoketest"
PORT = int(os.environ.get("SMOKE_PORT", "3200"))
BASE = f"http://127.0.0.1:{PORT}"
KEY = "sk-" + "smoketest" + secrets.token_hex(12)      # runtime canary, key-shaped on purpose
KEY_PATTERNS = re.compile(r"nvapi-[A-Za-z0-9_\-]{6,}|AIza[0-9A-Za-z_\-]{10,}|\bsk-[A-Za-z0-9_\-]{8,}|gsk_[A-Za-z0-9]{8,}")
results: list[tuple[str, bool, str]] = []


def sh(*args, check=True, capture=True):
    r = subprocess.run(args, capture_output=capture, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(args[:4])} ...\n{r.stderr[-2000:]}")
    return r


def record(name, ok, info=""):
    results.append((name, ok, info))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {info}" if info else ""), flush=True)


def http(path, method="GET", body=None, headers=None, timeout=30):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
]


def browser_check():
    """Optional: load the UI in a headless browser (throwaway profile) and confirm the JS ran under the CSP."""
    exe = next((b for b in BROWSERS if os.path.exists(b)), None)
    if not exe:
        print("[SKIP] headless browser check - no Chrome/Edge/Chromium found")
        return
    with tempfile.TemporaryDirectory(prefix="orrery-smoke-profile-") as prof:
        r = subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                            f"--user-data-dir={prof}", "--enable-logging=stderr", "--v=0",
                            "--virtual-time-budget=5000", "--dump-dom", BASE + "/"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    dom, console = r.stdout, r.stderr
    csp_errors = [l for l in console.splitlines() if "Content Security Policy" in l or "Refused to" in l]
    js_errors = [l for l in console.splitlines() if "Uncaught" in l]
    rendered = 'id="pickerName">Mock Chat<' in dom and 'id="appVersion">v1.0.0<' in dom
    record("browser: UI renders models (JS ran)", rendered)
    record("browser: no CSP violations / JS errors", not csp_errors and not js_errors,
           "; ".join((csp_errors + js_errors))[:300])


def cleanup():
    for c in (APP, MOCK, APP + "-lan"):
        sh("docker", "rm", "-f", c, check=False)
    sh("docker", "network", "rm", NET, check=False)
    sh("docker", "image", "rm", "-f", IMAGE, check=False)


def main() -> int:
    envfile = None
    try:
        cleanup()
        sh("docker", "build", "-q", "-t", IMAGE, ROOT)
        record("docker build", True)

        files = sh("docker", "run", "--rm", "--entrypoint", "sh", IMAGE, "-c",
                   "find / -xdev \\( -name '.env' -o -name '*.env' -o -name '.env.*' \\) 2>/dev/null; ls -A /app").stdout
        found_env = [l for l in files.splitlines() if l.startswith("/") and ".env" in l]
        app_files = sorted(l for l in files.splitlines() if not l.startswith("/"))
        record("no .env inside image", not found_env, f"/app contains: {', '.join(app_files)}")
        inspect = sh("docker", "image", "inspect", IMAGE).stdout
        record("image config has no secrets", "API_KEY" not in inspect and not KEY_PATTERNS.search(inspect))
        user = json.loads(inspect)[0]["Config"]["User"]
        record("image runs as non-root", user == "orrery", f"user={user}")

        # Env vars go in through a temp --env-file so the canary never appears on a command line.
        fd, envfile = tempfile.mkstemp(prefix="orrery-smoke-", suffix=".env")
        with os.fdopen(fd, "w") as fh:
            fh.write(f"MOCK_EXPECTED_KEY={KEY}\nORRERY_PROVIDERS=mock\nMOCK_LABEL=Mock\n"
                     f"MOCK_BASE_URL=http://{MOCK}:9000/v1\nMOCK_API_KEY={KEY}\n"
                     "MOCK_MODELS=mock-chat,mock-vision,mock-echo-key\nMOCK_VISION_MODELS=mock-vision\nORRERY_BIND=127.0.0.1\n")

        sh("docker", "network", "create", NET)
        sh("docker", "run", "-d", "--name", MOCK, "--network", NET, "--env-file", envfile,
           "-v", f"{os.path.join(ROOT, 'tests')}:/app/tests:ro", IMAGE,
           "uvicorn", "tests.mock_provider:app", "--host", "0.0.0.0", "--port", "9000")
        sh("docker", "run", "-d", "--name", APP, "--network", NET, "--env-file", envfile,
           "-p", f"127.0.0.1:{PORT}:8000", "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges:true", IMAGE)

        status = ""
        for _ in range(60):
            status = sh("docker", "inspect", "-f", "{{.State.Health.Status}}", APP, check=False).stdout.strip()
            if status == "healthy":
                break
            time.sleep(1)
        record("container healthy (Docker HEALTHCHECK)", status == "healthy", status)

        code, hdrs, body = http("/healthz")
        record("GET /healthz", code == 200 and json.loads(body)["status"] == "ok", body.strip())
        hl = {k.lower(): v for k, v in hdrs.items()}
        record("security headers", "frame-ancestors 'none'" in hl.get("content-security-policy", "")
               and hl.get("x-content-type-options") == "nosniff" and hl.get("referrer-policy") == "no-referrer")

        code, _, page = http("/")
        record("UI served", code == 200 and "<title>Orrery</title>" in page)

        code, _, models = http("/api/models")
        record("models listed", code == 200 and "mock-chat" in models, f"{len(json.loads(models)['models'])} models")

        code, _, stream = http("/api/chat", "POST", {"provider": "mock", "model": "mock-chat",
                                                     "messages": [{"role": "user", "content": "hello"}]})
        text = "".join(json.loads(b[5:]).get("c", "") for b in stream.split("\n\n")
                       if b.startswith("data:") and b[5:].strip() != "[DONE]")
        record("streamed reply via mock provider", code == 200 and text == "Hello from the mock provider.", repr(text))

        code, _, echo = http("/api/chat", "POST", {"provider": "mock", "model": "mock-echo-key",
                                                   "messages": [{"role": "user", "content": "hello"}]})
        record("leaky provider error is redacted", "rejected the API key" in echo and KEY not in echo)

        browser_check()

        code, _, _ = http("/", headers={"Host": "evil.example"})
        record("foreign Host header rejected (DNS rebinding)", code == 421, str(code))

        code, _, _ = http("/api/chat", "POST", {"provider": "mock", "model": "mock-chat", "messages": []},
                          headers={"Origin": "http://evil.example"})
        record("cross-origin POST rejected", code == 403, str(code))

        logs = sh("docker", "logs", APP, check=False)
        all_out = "\n".join([models, stream, echo, page, logs.stdout, logs.stderr])
        record("no key in responses or container logs", KEY not in all_out and not KEY_PATTERNS.search(all_out))

        # LAN mode without a password must refuse to start.
        sh("docker", "run", "-d", "--name", APP + "-lan", "-e", "ORRERY_BIND=0.0.0.0", IMAGE)
        for _ in range(20):
            st = sh("docker", "inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", APP + "-lan").stdout.split()
            if st[0] == "exited":
                break
            time.sleep(1)
        lan_logs = sh("docker", "logs", APP + "-lan", check=False)
        record("LAN mode without APP_PASSWORD refuses to start", st[0] == "exited" and st[1] != "0"
               and "APP_PASSWORD" in lan_logs.stdout + lan_logs.stderr, " ".join(st))
    except Exception as e:  # report and fall through to cleanup
        record("smoke test aborted", False, str(e)[:500])
    finally:
        cleanup()
        if envfile and os.path.exists(envfile):
            os.remove(envfile)
        left = sh("docker", "ps", "-a", "--filter", "name=orrery-smoketest", "-q").stdout.strip()
        img = sh("docker", "images", "-q", IMAGE).stdout.strip()
        record("cleanup (containers, network, image removed)", not left and not img)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
