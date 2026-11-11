"""Backend unit tests. Everything runs against tests/mock_provider.py - no network, no real keys.

Run:  pip install -r requirements-dev.txt && pytest -q
"""
import base64
import json
import logging
import re
import secrets

import httpx
import pytest
from fastapi.testclient import TestClient

import app as orrery
from tests import mock_provider

# Canary secrets are generated at runtime so no key-like string ever lives in the repo.
CANARY = "canary" + secrets.token_hex(16)              # exact-value redaction
KEYLIKE_CANARY = "sk-" + "orrerytest" + secrets.token_hex(12)   # pattern redaction
PASSWORD = "correct horse " + secrets.token_hex(4)
WRONG_PASSWORD = "wrong-" + secrets.token_hex(4)

# Anything that looks like a real provider key must never show up in a response or log.
KEY_PATTERNS = re.compile(r"nvapi-[A-Za-z0-9_\-]{6,}|AIza[0-9A-Za-z_\-]{10,}|\bsk-[A-Za-z0-9_\-]{8,}|gsk_[A-Za-z0-9]{8,}")


def base_env(**extra):
    env = {
        "ORRERY_PROVIDERS": "mock",
        "MOCK_LABEL": "Mock",
        "MOCK_BASE_URL": "http://mock.test/v1",
        "MOCK_API_KEY": CANARY,
        "MOCK_MODELS": "mock-chat,mock-vision,mock-echo-key,mock-ratelimit",
        "MOCK_VISION_MODELS": "mock-vision",
        "ORRERY_ALLOWED_HOSTS": "testserver",
    }
    env.update(extra)
    return env


@pytest.fixture
def make(monkeypatch):
    """Build a TestClient for a given environment, wired to the mock provider."""
    clients = []

    def _make(env=None, expected_key=None):
        env = env or base_env()
        monkeypatch.setenv("MOCK_EXPECTED_KEY", expected_key if expected_key is not None else env.get("MOCK_API_KEY", ""))
        orrery.reload_settings(env)
        monkeypatch.setattr(orrery, "make_client", lambda **kw: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mock_provider.app), **kw))
        c = TestClient(orrery.app)
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)
    orrery.reload_settings({})


def chat(client, model="mock-chat", messages=None, **kw):
    body = {"provider": "mock", "model": model,
            "messages": messages if messages is not None else [{"role": "user", "content": "hi"}]}
    return client.post("/api/chat", json=body, **kw)


def sse_events(text):
    out = []
    for block in text.split("\n\n"):
        if block.startswith("data:"):
            data = block[5:].strip()
            out.append(data if data == "[DONE]" else json.loads(data))
    return out


def png_data_url(n_bytes=64):
    return "data:image/png;base64," + base64.b64encode(b"\x89PNG" + b"0" * n_bytes).decode()


# ---------------------------------------------------------------------------------------------
# Provider config parsing
# ---------------------------------------------------------------------------------------------
def test_provider_parsing_rules():
    env = {
        "ORRERY_PROVIDERS": "good, nokey, local, placeholder, badurl, creds, Bad-Id!, query",
        "GOOD_BASE_URL": "https://api.example.test/v1/", "GOOD_API_KEY": CANARY, "GOOD_MODELS": "a, b ,,c",
        "NOKEY_BASE_URL": "https://api.example.test/v1",
        "LOCAL_BASE_URL": "http://host.docker.internal:11434/v1", "LOCAL_NO_KEY": "true", "LOCAL_LABEL": "My box",
        "PLACEHOLDER_BASE_URL": "https://x.test/v1", "PLACEHOLDER_API_KEY": "nvapi-REPLACE_ME",
        "BADURL_BASE_URL": "ftp://x.test", "BADURL_API_KEY": "k" * 10,
        "CREDS_BASE_URL": "https://user:pw@x.test/v1", "CREDS_API_KEY": "k" * 10,
        "QUERY_BASE_URL": "https://x.test/v1?key=abc", "QUERY_API_KEY": "k" * 10,
    }
    provs, warnings = orrery.parse_providers(env)
    assert set(provs) == {"good", "local"}
    assert provs["good"].base == "https://api.example.test/v1"
    assert provs["good"].models == ["a", "b", "c"]
    assert provs["good"].label == "Good"
    assert provs["local"].key == "" and provs["local"].models is None and provs["local"].label == "My box"
    joined = " ".join(warnings)
    for pid in ("nokey", "placeholder", "badurl", "creds", "query"):
        assert pid in joined
    assert CANARY not in joined and CANARY not in repr(provs)


def test_unset_providers_list_means_no_providers():
    provs, warnings = orrery.parse_providers({"NVIDIA_API_KEY": CANARY})
    assert provs == {} and warnings == []


def test_catalog_loading(tmp_path):
    f = tmp_path / "models.json"
    f.write_text(json.dumps({"groups": ["Fast"], "models": {
        "m1": {"name": "Model One", "desc": "Best for tests", "group": "Fast", "vision": True},
        "m2": {"name": "x" * 500}, "bad": "not-a-dict"}}), encoding="utf-8")
    cat, groups = orrery.load_catalog(str(f))
    assert groups == ["Fast"]
    assert cat["m1"] == {"name": "Model One", "desc": "Best for tests", "group": "Fast", "vision": True, "rank": 0}
    assert len(cat["m2"]["name"]) == 80 and "bad" not in cat
    f.write_text("{ not json", encoding="utf-8")
    assert orrery.load_catalog(str(f)) == ({}, [])
    assert orrery.load_catalog(str(tmp_path / "missing.json")) == ({}, [])


def test_default_catalog_is_valid_json():
    cat, groups = orrery.load_catalog(orrery.DEFAULT_CATALOG)
    assert len(cat) > 10 and "Everyday" in groups


# ---------------------------------------------------------------------------------------------
# Models + whitelist
# ---------------------------------------------------------------------------------------------
def test_models_endpoint(make):
    c = make()
    r = c.get("/api/models")
    assert r.status_code == 200
    j = r.json()
    ids = [m["model"] for m in j["models"]]
    assert ids == ["mock-chat", "mock-vision", "mock-echo-key", "mock-ratelimit"]
    vision = {m["model"]: m["vision"] for m in j["models"]}
    assert vision["mock-vision"] is True and vision["mock-chat"] is False
    assert j["auth"] is False
    assert CANARY not in r.text and "key" not in json.dumps(j["models"]).replace("mock-echo-key", "")


def test_model_discovery_when_no_list(make):
    env = base_env()
    env.pop("MOCK_MODELS")
    c = make(env)
    ids = [m["model"] for m in c.get("/api/models").json()["models"]]
    assert set(ids) == set(mock_provider.MODELS)


def test_model_whitelist(make):
    c = make()
    assert chat(c, model="not-enabled").status_code == 400
    r = c.post("/api/chat", json={"provider": "nope", "model": "mock-chat", "messages": []})
    assert r.status_code == 400 and "not configured" in r.json()["detail"]
    assert c.post("/api/chat", json={"provider": ["x"], "model": "mock-chat"}).status_code == 400


# ---------------------------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------------------------
def test_streamed_reply(make):
    c = make()
    r = chat(c)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = sse_events(r.text)
    assert "".join(e.get("c", "") for e in ev if isinstance(e, dict)) == "Hello from the mock provider."
    assert any(isinstance(e, dict) and e.get("r") for e in ev)
    assert ev[-1] == "[DONE]"


def test_rate_limit_error_is_friendly(make):
    c = make()
    ev = sse_events(chat(c, model="mock-ratelimit").text)
    assert "rate limit" in ev[0]["error"]


def test_system_prompt_is_prepended(make, monkeypatch):
    seen = {}
    real = mock_provider.app

    async def spy(scope, receive, send):
        if scope["type"] == "http" and scope["path"].endswith("/chat/completions"):
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if not msg.get("more_body"):
                    break
            seen.update(json.loads(body))
            sent = False

            async def replay():
                nonlocal sent
                if sent:
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await real(scope, replay, send)
        return await real(scope, receive, send)

    c = make(base_env(SYSTEM_PROMPT="Be brief."))
    monkeypatch.setattr(orrery, "make_client", lambda **kw: httpx.AsyncClient(transport=httpx.ASGITransport(app=spy), **kw))
    chat(c, messages=[{"role": "user", "content": "hi", "extra": "dropped"}, {"role": "system", "content": "evil"}])
    assert seen["messages"][0] == {"role": "system", "content": "Be brief."}
    assert seen["messages"][1] == {"role": "user", "content": "hi"}       # extra fields and browser 'system' dropped
    assert len(seen["messages"]) == 2


# ---------------------------------------------------------------------------------------------
# Input validation + image limits
# ---------------------------------------------------------------------------------------------
def test_body_validation(make):
    c = make()
    h = {"Content-Type": "application/json"}
    assert c.post("/api/chat", content=b"{nope", headers=h).status_code == 400
    assert c.post("/api/chat", content=b"[1,2]", headers=h).status_code == 400
    assert c.post("/api/chat", content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert chat(c, messages="hi").status_code == 400
    assert chat(c, messages=[{"role": "user", "content": "x"}] * 201).status_code == 400
    assert chat(c, messages=[{"role": "user", "content": "x" * 200_001}]).status_code == 413
    assert chat(c, messages=[{"role": "system", "content": "only system"}]).status_code == 400   # nothing left to send


def test_body_size_limit(make):
    c = make()
    big = b'{"x":"' + b"a" * (8 * 1024 * 1024 + 10) + b'"}'
    r = c.post("/api/chat", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def _img_msg(urls, text="what is this?"):
    return [{"role": "user", "content": [{"type": "text", "text": text}] +
             [{"type": "image_url", "image_url": {"url": u}} for u in urls]}]


def test_images_accepted_for_vision_model(make):
    c = make()
    ev = sse_events(chat(c, model="mock-vision", messages=_img_msg([png_data_url(), png_data_url()])).text)
    assert "I can see 2 image(s)." in "".join(e.get("c", "") for e in ev if isinstance(e, dict))


def test_image_rules(make):
    c = make()
    assert chat(c, model="mock-chat", messages=_img_msg([png_data_url()])).status_code == 400          # not a vision model
    assert chat(c, model="mock-vision", messages=_img_msg([png_data_url()] * 4)).status_code == 400   # > 3 images
    for bad in ("https://example.test/cat.png", "data:image/svg+xml;base64,PHN2Zz4=", "data:image/png;base64,***"):
        assert chat(c, model="mock-vision", messages=_img_msg([bad])).status_code == 400
    assert chat(c, model="mock-vision", messages=[{"role": "assistant", "content": [{"type": "text", "text": "x"}]}]).status_code == 400
    assert chat(c, model="mock-vision", messages=[{"role": "user", "content": [{"type": "video"}]}]).status_code == 400
    two_mb = png_data_url(2 * 1024 * 1024)
    r = chat(c, model="mock-vision", messages=_img_msg([two_mb, two_mb, two_mb]))                    # ~6 MB decoded
    assert r.status_code == 413 and "too large" in r.json()["detail"]


# ---------------------------------------------------------------------------------------------
# Security headers, host check, cross-origin
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/", "/api/models", "/healthz", "/static/app.js", "/does-not-exist"])
def test_security_headers(make, path):
    r = make().get(path)
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"


def test_api_responses_not_cached(make):
    assert make().get("/api/models").headers["cache-control"] == "no-store"


def test_host_header_check_blocks_dns_rebinding(make):
    c = make()
    assert c.get("/", headers={"Host": "evil.example"}).status_code == 421
    assert c.get("/healthz", headers={"Host": "127.0.0.1:3000"}).status_code == 200
    assert c.get("/healthz", headers={"Host": "localhost"}).status_code == 200
    assert c.get("/healthz", headers={"Host": "[::1]:3000"}).status_code == 200


def test_cross_origin_post_blocked(make):
    c = make()
    r = chat(c, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert chat(c, headers={"Origin": "http://testserver"}).status_code == 200


# ---------------------------------------------------------------------------------------------
# Password gate + LAN mode
# ---------------------------------------------------------------------------------------------
def test_password_hash_roundtrip():
    h = orrery.hash_password(PASSWORD)
    assert h.startswith("scrypt:") and PASSWORD not in h and "$" not in h
    assert orrery.verify_password(PASSWORD, h)
    assert not orrery.verify_password(PASSWORD + "x", h)
    assert not orrery.verify_password(PASSWORD, "scrypt:garbage")
    assert not orrery.verify_password(PASSWORD, "md5:1:1:1:aa:bb")
    assert orrery.hash_password(PASSWORD) != h                   # random salt


def test_password_gate(make):
    c = make(base_env(APP_PASSWORD=PASSWORD))
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/models").status_code == 401
    assert chat(c).status_code == 401
    assert c.get("/static/app.js", follow_redirects=False).status_code == 303
    assert c.get("/healthz").status_code == 200
    assert c.get("/login").status_code == 200
    assert c.get("/static/login.js").status_code == 200

    assert c.post("/api/login", json={"password": WRONG_PASSWORD}).status_code == 401
    r = c.post("/api/login", json={"password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/api/models").json()["auth"] is True
    assert chat(c).status_code == 200

    c.post("/api/logout", json={})
    assert c.get("/api/models").status_code == 401


def test_password_hash_env(make):
    c = make(base_env(APP_PASSWORD_HASH=orrery.hash_password(PASSWORD)))
    assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200


def test_login_rate_limit(make):
    c = make(base_env(APP_PASSWORD=PASSWORD))
    for _ in range(5):
        assert c.post("/api/login", json={"password": WRONG_PASSWORD}).status_code == 401
    r = c.post("/api/login", json={"password": PASSWORD})          # even the right password is refused now
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_limiter_window_expires():
    lim = orrery.LoginLimiter(per_client=2, total=100, window=60)
    lim.failed("a", now=0)
    lim.failed("a", now=1)
    assert lim.retry_after("a", now=2) > 0
    assert lim.retry_after("b", now=2) == 0
    assert lim.retry_after("a", now=100) == 0


def test_global_login_limit():
    lim = orrery.LoginLimiter(per_client=5, total=3, window=60)
    for ip in ("a", "b", "c"):
        lim.failed(ip, now=0)
    assert lim.retry_after("d", now=1) > 0


def test_lan_mode_requires_password():
    s = orrery.load_settings(base_env(ORRERY_BIND="0.0.0.0"))
    assert s.lan_mode and any("APP_PASSWORD" in e for e in orrery.config_errors(s))
    s = orrery.load_settings(base_env(ORRERY_BIND="0.0.0.0", APP_PASSWORD=PASSWORD, ORRERY_ALLOWED_HOSTS=""))
    assert orrery.config_errors(s) == [] and s.allowed_hosts is None     # any host; the password protects it
    s = orrery.load_settings(base_env(ORRERY_BIND="0.0.0.0", APP_PASSWORD=PASSWORD, ORRERY_ALLOWED_HOSTS="orrery.lan"))
    assert s.allowed_hosts == {"orrery.lan", "localhost", "127.0.0.1", "::1"}
    s = orrery.load_settings(base_env(ORRERY_BIND="127.0.0.1"))
    assert not s.lan_mode and orrery.config_errors(s) == []
    s = orrery.load_settings(base_env(APP_PASSWORD="short"))
    assert any("8 characters" in e for e in orrery.config_errors(s))


def test_lan_mode_without_password_refuses_to_start(monkeypatch):
    orrery.reload_settings(base_env(ORRERY_BIND="0.0.0.0"))
    try:
        with pytest.raises(RuntimeError):
            with TestClient(orrery.app):
                pass
    finally:
        orrery.reload_settings({})


def test_password_not_kept_in_settings():
    s = orrery.load_settings(base_env(APP_PASSWORD=PASSWORD))
    assert PASSWORD not in repr(s) and PASSWORD not in (s.password_hash or "")


# ---------------------------------------------------------------------------------------------
# Key-leak tests
# ---------------------------------------------------------------------------------------------
def test_redact_patterns():
    orrery.reload_settings(base_env())
    try:
        samples = {
            f"key is {CANARY}!": CANARY,
            "Authorization: Bearer abcdef123456789": "abcdef123456789",
            "https://x.test/v1?key=SECRETVALUE123&x=1": "SECRETVALUE123",
            f"Incorrect API key provided: {KEYLIKE_CANARY}": KEYLIKE_CANARY,
            "nvapi-" + "Q" * 30: "Q" * 30,
            "gsk_" + "Z" * 30: "Z" * 30,
            "AIza" + "Y" * 30: "Y" * 30,
            '"api_key": "abcdefghijkl"': "abcdefghijkl",
        }
        for text, secret in samples.items():
            out = orrery.redact(text)
            assert secret not in out, out
            assert "****" in out
    finally:
        orrery.reload_settings({})


@pytest.mark.parametrize("key", [CANARY, KEYLIKE_CANARY])
def test_no_key_leak_in_responses_or_logs(make, caplog, key):
    """Drive every endpoint, including a provider that echoes the key back, and sweep all output."""
    caplog.set_level(logging.DEBUG)
    c = make(base_env(MOCK_API_KEY=key, APP_PASSWORD=PASSWORD))
    texts = []
    for r in (
        c.get("/login"), c.post("/api/login", json={"password": WRONG_PASSWORD}),
        c.post("/api/login", json={"password": PASSWORD}),
        c.get("/"), c.get("/api/models"), c.get("/healthz"), c.get("/static/app.js"),
        chat(c), chat(c, model="mock-echo-key"), chat(c, model="mock-ratelimit"), chat(c, model="nope"),
        chat(c, model="mock-vision", messages=_img_msg(["https://bad.test/x.png"])),
        c.get("/", headers={"Host": "evil.example"}),
    ):
        texts.append(r.text)
        texts.extend(f"{k}: {v}" for k, v in r.headers.items())

    # Wrong key at the provider -> provider echoes it -> Orrery must scrub it.
    c2 = make(base_env(MOCK_API_KEY=key), expected_key="something-else")
    for r in (chat(c2), c2.get("/api/models")):
        texts.append(r.text)

    blob = "\n".join(texts) + "\n" + caplog.text
    assert key not in blob
    assert PASSWORD not in blob
    assert not KEY_PATTERNS.search(blob), KEY_PATTERNS.search(blob).group(0)[:8] + "****"
    # And the echo test really exercised the leak path:
    echo = [t for t in texts if "rejected the API key" in t]
    assert echo and all("****" in t for t in echo)
