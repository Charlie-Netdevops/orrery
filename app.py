"""Orrery - a lightweight, self-hosted AI chat. Your models, in orbit.

One small FastAPI server that:
  * serves the chat UI from ./static (no CDNs, everything is local)
  * forwards chats to any OpenAI-compatible provider configured in .env
  * keeps API keys on the server: they are never sent to the browser, never
    logged, and scrubbed from every error message before it leaves the server.

Configuration lives entirely in environment variables (see .env.example).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

VERSION = "1.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DEFAULT_CATALOG = os.path.join(BASE_DIR, "config", "models.json")

# ---- Request limits -------------------------------------------------------------------------
MAX_IMAGES_PER_MESSAGE = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024        # total decoded image data per request
MAX_BODY_BYTES = 8 * 1024 * 1024         # whole request body (base64 adds ~33%)
MAX_MESSAGES = 200                       # messages per request
MAX_TEXT_CHARS = 200_000                 # characters per text part
MAX_ID_CHARS = 200                       # provider / model id length
DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$")
PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PLACEHOLDER_RE = re.compile(r"(?i)replace[_-]?me|your[_-]?(api[_-]?)?key|^<.*>$|^changeme$")
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}

log = logging.getLogger("orrery")


# =============================================================================================
# Secret scrubbing - applied to every error message sent to the browser and to every log line.
# =============================================================================================
_KEYLIKE = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]{6,}"),
    re.compile(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._\-]{6,}"),
    re.compile(r"(?i)([?&](?:key|api_key|apikey|token|access_token)=)[^&\s\"'<>]+"),
    re.compile(r"()\b(?:nvapi-|sk-|gsk_|AIza|xai-|hf_|pplx-)[A-Za-z0-9_\-]{4,}"),
]


def redact(text: str) -> str:
    """Remove configured secrets and anything that looks like an API key."""
    text = str(text)
    for secret in SETTINGS.secrets if "SETTINGS" in globals() else ():
        text = text.replace(secret, "****")
    for rx in _KEYLIKE:
        text = rx.sub(lambda m: m.group(1) + "****", text)
    return text


class RedactFilter(logging.Filter):
    """Logging filter: scrub secrets from every record before it is written."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - malformed record, let logging handle it
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        return True


def _install_log_filters() -> None:
    f = RedactFilter()
    for name in ("", "orrery", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"):
        lg = logging.getLogger(name)
        if not any(isinstance(x, RedactFilter) for x in lg.filters):
            lg.addFilter(f)
        for h in lg.handlers:
            if not any(isinstance(x, RedactFilter) for x in h.filters):
                h.addFilter(f)
    logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs every request URL at INFO
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# =============================================================================================
# Configuration
# =============================================================================================
def _split(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _is_placeholder(value: str) -> bool:
    return bool(value) and bool(PLACEHOLDER_RE.search(value))


@dataclass
class Provider:
    id: str
    label: str
    base: str
    key: str = field(repr=False)          # never shown in repr / tracebacks
    models: list[str] | None              # None = discover from GET {base}/models
    vision: set[str] = field(default_factory=set)

    def __repr__(self) -> str:            # belt and braces: no key, ever
        return f"Provider(id={self.id!r}, label={self.label!r}, base={self.base!r}, key=****)"


def parse_providers(env=None) -> tuple[dict[str, Provider], list[str]]:
    """Read ORRERY_PROVIDERS=a,b and <ID>_BASE_URL / _API_KEY / _MODELS / _LABEL / _VISION_MODELS / _NO_KEY.

    Returns (providers, warnings). Warnings never contain secret values.
    """
    env = os.environ if env is None else env
    providers: dict[str, Provider] = {}
    warnings: list[str] = []
    for raw in _split(env.get("ORRERY_PROVIDERS", "")):
        pid = raw.lower()
        if not PROVIDER_ID_RE.match(pid):
            warnings.append(f"Ignoring provider id {raw[:40]!r}: use lowercase letters, digits and _ (max 32).")
            continue
        if pid in providers:
            continue
        P = pid.upper()
        base = env.get(f"{P}_BASE_URL", "").strip().rstrip("/")
        key = env.get(f"{P}_API_KEY", "").strip()
        if _is_placeholder(key):
            key = ""
        no_key = _truthy(env.get(f"{P}_NO_KEY"))
        if not base:
            warnings.append(f"{pid}: {P}_BASE_URL is empty - provider disabled.")
            continue
        u = urlsplit(base)
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment:
            warnings.append(f"{pid}: {P}_BASE_URL must be a plain http(s) URL with no credentials or query - provider disabled.")
            continue
        if not key and not no_key:
            warnings.append(f"{pid}: {P}_API_KEY is empty - provider disabled (set {P}_NO_KEY=true for local servers).")
            continue
        if re.search(r"\s", key):
            warnings.append(f"{pid}: {P}_API_KEY contains spaces or line breaks - provider disabled.")
            continue
        label = env.get(f"{P}_LABEL", "").strip()[:40] or pid.replace("_", " ").title()
        models = [m for m in _split(env.get(f"{P}_MODELS", "")) if len(m) <= MAX_ID_CHARS] or None
        vision = set(_split(env.get(f"{P}_VISION_MODELS", "")))
        providers[pid] = Provider(pid, label, base, key, models, vision)
    return providers, warnings


def load_catalog(path: str) -> tuple[dict[str, dict], list[str]]:
    """Friendly names, 'best for' lines, picker groups and vision flags (config/models.json)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}, []
    except (OSError, ValueError) as e:
        log.warning("Model catalog %s could not be read (%s) - showing raw model IDs.", path, e.__class__.__name__)
        return {}, []
    if not isinstance(data, dict):
        return {}, []
    out: dict[str, dict] = {}
    raw = data.get("models", {})
    for i, (mid, info) in enumerate(raw.items() if isinstance(raw, dict) else []):
        if not isinstance(mid, str) or not isinstance(info, dict) or len(mid) > MAX_ID_CHARS:
            continue
        out[mid] = {
            "name": str(info.get("name", ""))[:80],
            "desc": str(info.get("desc", ""))[:160],
            "group": str(info.get("group", ""))[:40],
            "vision": info.get("vision") is True,
            "rank": i,
        }
    groups = [str(g)[:40] for g in data.get("groups", []) if isinstance(g, str)][:20]
    return out, groups


# ---- Password hashing (scrypt, stdlib only) --------------------------------------------------
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:{_b64(salt)}:{_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time comparison of scrypt hashes."""
    try:
        scheme, n, r, p, salt, want = stored.split(":")
        n, r, p = int(n), int(r), int(p)
        if scheme != "scrypt" or not (2 ** 10 <= n <= 2 ** 20) or not (1 <= r <= 32) or not (1 <= p <= 16):
            return False
        want_b = _unb64(want)
        got = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=n, r=r, p=p,
                             dklen=len(want_b), maxmem=256 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want_b)


def _is_loopback(host: str) -> bool:
    host = host.strip().strip("[]").lower()
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class Settings:
    providers: dict[str, Provider]
    warnings: list[str]
    catalog: dict[str, dict]
    groups: list[str]
    system_prompt: str
    bind: str
    lan_mode: bool
    password_hash: str | None = field(repr=False)
    password_error: str | None
    allowed_hosts: set[str] | None          # None = any host (LAN mode without an explicit list)
    session_seconds: int
    cookie_secure: bool
    secrets: list[str] = field(repr=False)


def load_settings(env=None) -> Settings:
    env = os.environ if env is None else env
    providers, warnings = parse_providers(env)
    catalog, groups = load_catalog(env.get("ORRERY_MODEL_CATALOG", "").strip() or DEFAULT_CATALOG)
    bind = env.get("ORRERY_BIND", "").strip() or "127.0.0.1"
    lan_mode = not _is_loopback(bind)

    password_hash, password_error = None, None
    pw_hash = env.get("APP_PASSWORD_HASH", "").strip()
    pw = env.get("APP_PASSWORD", "")
    if _is_placeholder(pw):
        pw = ""
    if pw_hash:
        if pw_hash.startswith("scrypt:") and pw_hash.count(":") == 5:
            password_hash = pw_hash
        else:
            password_error = "APP_PASSWORD_HASH is not a valid hash. Generate one with: python app.py hash-password"
    elif pw:
        if len(pw) < 8:
            password_error = "APP_PASSWORD must be at least 8 characters."
        else:
            password_hash = hash_password(pw)   # random salt per start; plaintext is not kept by Orrery

    extra_hosts = {h.lower() for h in _split(env.get("ORRERY_ALLOWED_HOSTS", ""))}
    if lan_mode:
        allowed = (extra_hosts | LOOPBACK_NAMES) if extra_hosts else None
    else:
        allowed = LOOPBACK_NAMES | extra_hosts

    secret_values = [p.key for p in providers.values() if len(p.key) >= 4]
    # Also scrub keys of providers that ended up disabled, and the password itself.
    for k, v in env.items():
        if (k.endswith("_API_KEY") or k in ("APP_PASSWORD", "APP_PASSWORD_HASH")) and len(v.strip()) >= 4:
            secret_values.append(v.strip())
    secret_values = sorted(set(secret_values), key=len, reverse=True)

    try:
        hours = float(env.get("ORRERY_SESSION_HOURS", "") or 168)
    except ValueError:
        hours = 168
    return Settings(
        providers=providers,
        warnings=warnings,
        catalog=catalog,
        groups=groups,
        system_prompt=env.get("SYSTEM_PROMPT", "").strip(),
        bind=bind,
        lan_mode=lan_mode,
        password_hash=password_hash,
        password_error=password_error,
        allowed_hosts=allowed,
        session_seconds=int(max(0.25, min(hours, 24 * 90)) * 3600),
        cookie_secure=_truthy(env.get("ORRERY_COOKIE_SECURE")),
        secrets=secret_values,
    )


def config_errors(s: Settings) -> list[str]:
    """Problems that must stop the server from starting."""
    errors = []
    if s.password_error:
        errors.append(s.password_error)
    if s.lan_mode and not s.password_hash:
        errors.append(
            f"LAN mode (ORRERY_BIND={s.bind}) makes Orrery reachable by other devices on your network. "
            "Set APP_PASSWORD (8+ characters) in .env, or set ORRERY_BIND=127.0.0.1."
        )
    return errors


SETTINGS = load_settings()


def reload_settings(env=None) -> Settings:
    """Re-read configuration (used by tests)."""
    global SETTINGS
    SETTINGS = load_settings(env)
    _sessions.clear()
    _model_cache.clear()
    LIMITER.reset_all()
    return SETTINGS


# =============================================================================================
# Login: sessions + rate limiting
# =============================================================================================
SESSION_COOKIE = "orrery_session"
_sessions: dict[str, float] = {}          # sha256(token) -> expiry timestamp


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_session() -> str:
    now = time.time()
    for k in [k for k, exp in _sessions.items() if exp < now]:
        del _sessions[k]
    if len(_sessions) > 1000:                                 # hard cap; drop the oldest
        for k in sorted(_sessions, key=_sessions.get)[: len(_sessions) - 1000]:
            del _sessions[k]
    token = secrets.token_urlsafe(32)
    _sessions[_token_id(token)] = now + SETTINGS.session_seconds
    return token


def _valid_session(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 128:
        return False
    exp = _sessions.get(_token_id(token))
    return bool(exp and exp > time.time())


class LoginLimiter:
    """At most `per_client` failed logins per client and `total` overall within `window` seconds."""

    def __init__(self, per_client: int = 5, total: int = 50, window: float = 900):
        self.per_client, self.total, self.window = per_client, total, window
        self.fails: dict[str, list[float]] = {}
        self.all: list[float] = []

    def reset_all(self) -> None:
        self.fails.clear()
        self.all.clear()

    def _prune(self, now: float) -> None:
        cut = now - self.window
        self.all = [t for t in self.all if t > cut]
        for ip in list(self.fails):
            self.fails[ip] = [t for t in self.fails[ip] if t > cut]
            if not self.fails[ip]:
                del self.fails[ip]

    def retry_after(self, client: str, now: float | None = None) -> int:
        now = time.time() if now is None else now
        self._prune(now)
        mine = self.fails.get(client, [])
        if len(mine) >= self.per_client:
            return int(mine[0] + self.window - now) + 1
        if len(self.all) >= self.total:
            return int(self.all[0] + self.window - now) + 1
        return 0

    def failed(self, client: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.fails.setdefault(client, []).append(now)
        self.all.append(now)
        if len(self.fails) > 10000:                          # memory cap
            self.fails.pop(next(iter(self.fails)))

    def succeeded(self, client: str) -> None:
        self.fails.pop(client, None)


LIMITER = LoginLimiter()


# =============================================================================================
# App + security middleware
# =============================================================================================
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "connect-src 'self'; font-src 'self'; media-src 'self' blob:; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=(), payment=(), usb=()",
}
PUBLIC_PATHS = {
    "/login", "/api/login", "/healthz", "/favicon.svg",
    "/static/login.js", "/static/app.css", "/static/logo.svg", "/static/favicon.svg",
}



async def _startup() -> None:
    _install_log_filters()
    s = SETTINGS
    errors = config_errors(s)
    if errors:
        for e in errors:
            log.error("Orrery cannot start: %s", e)
        raise RuntimeError("Invalid configuration - see the messages above.")
    for w in s.warnings:
        log.warning(w)
    if s.lan_mode:
        log.warning("LAN MODE: Orrery is reachable from other devices on your network (bind %s). "
                    "Login is required. Only use this on networks you trust.", s.bind)
    names = ", ".join(f"{p.label} ({len(p.models) if p.models else 'auto'})" for p in s.providers.values())
    log.info("Orrery %s ready. Providers: %s", VERSION, names or "none - edit .env")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _startup()
    yield


app = FastAPI(title="Orrery", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _host_only(host_header: str) -> str:
    h = host_header.strip().lower()
    if h.startswith("["):                       # [::1]:3000
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def _secure(resp: Response, api: bool = False) -> Response:
    for k, v in SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    if api:
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.middleware("http")
async def guard(request: Request, call_next):
    s, path = SETTINGS, request.url.path
    api = path.startswith("/api/")
    # 1) Host check: blocks DNS-rebinding pages from talking to a localhost-only Orrery.
    host = _host_only(request.headers.get("host", ""))
    if s.allowed_hosts is not None and host not in s.allowed_hosts:
        return _secure(JSONResponse({"detail": "Host not allowed. If you meant to open Orrery under this name, "
                                               "add it to ORRERY_ALLOWED_HOSTS in .env."}, 421), True)
    # 2) Cross-site write protection.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin is not None:
            o = urlsplit(origin)
            if o.netloc.lower() != request.headers.get("host", "").lower():
                return _secure(JSONResponse({"detail": "Cross-origin request blocked."}, 403), True)
        if api and request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return _secure(JSONResponse({"detail": "Content-Type must be application/json."}, 415), True)
    # 3) Password gate (only when APP_PASSWORD / APP_PASSWORD_HASH is set).
    if s.password_hash and path not in PUBLIC_PATHS and not _valid_session(request):
        if api:
            return _secure(JSONResponse({"detail": "Login required."}, 401), True)
        return _secure(RedirectResponse("/login", 303))
    resp = await call_next(request)
    return _secure(resp, api)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    log.error("Unhandled %s on %s", exc.__class__.__name__, request.url.path)
    return _secure(JSONResponse({"detail": "Something went wrong inside Orrery. Check the container logs."}, 500), True)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    detail = redact(exc.detail) if isinstance(exc.detail, str) else "Request failed."
    return _secure(JSONResponse({"detail": detail}, exc.status_code, headers=getattr(exc, "headers", None)),
                   request.url.path.startswith("/api/"))


# =============================================================================================
# Pages
# =============================================================================================
@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/login")
async def login_page():
    if not SETTINGS.password_hash:
        return RedirectResponse("/", 303)
    return FileResponse(os.path.join(STATIC_DIR, "login.html"))


@app.get("/favicon.svg")
async def favicon():
    return FileResponse(os.path.join(STATIC_DIR, "favicon.svg"), media_type="image/svg+xml")


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "version": VERSION}


async def _read_json(request: Request, limit: int) -> dict:
    """Read a JSON object body without ever buffering more than `limit` bytes."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"Request is too large ({int(declared) / 1048576:.1f} MB). "
                                 "Images are limited to 5 MB per message.")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "Request is too large. Images are limited to 5 MB per message.")
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks))
    except ValueError:
        raise HTTPException(400, "Request body must be valid JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "Request body must be a JSON object")
    return body


# =============================================================================================
# Login API
# =============================================================================================
@app.post("/api/login")
async def login(request: Request):
    s = SETTINGS
    if not s.password_hash:
        return {"ok": True}
    client = request.client.host if request.client else "unknown"
    wait = LIMITER.retry_after(client)
    if wait:
        raise HTTPException(429, f"Too many failed logins. Try again in {max(1, wait // 60)} minute(s).",
                            headers={"Retry-After": str(wait)})
    body = await _read_json(request, 4096)
    pw = body.get("password")
    if not isinstance(pw, str) or not pw or len(pw) > 1024:
        raise HTTPException(400, "Enter the password.")
    ok = await asyncio.to_thread(verify_password, pw, s.password_hash)
    if not ok:
        LIMITER.failed(client)
        log.warning("Failed login attempt from %s", client)
        raise HTTPException(401, "Wrong password.")
    LIMITER.succeeded(client)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, _new_session(), max_age=s.session_seconds, httponly=True,
                    samesite="strict", secure=s.cookie_secure or request.url.scheme == "https", path="/")
    return resp


@app.post("/api/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        _sessions.pop(_token_id(token), None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# =============================================================================================
# Models
# =============================================================================================
_model_cache: dict[str, tuple[float, list[str]]] = {}


def make_client(**kwargs) -> httpx.AsyncClient:
    """Outbound HTTP client (tests swap this for a mock transport)."""
    return httpx.AsyncClient(follow_redirects=False, **kwargs)


def _auth_headers(p: Provider) -> dict:
    return {"Authorization": f"Bearer {p.key}"} if p.key else {}


async def discover_models(p: Provider) -> list[str]:
    """For providers without an explicit model list: GET {base}/models (cached for 60 s)."""
    hit = _model_cache.get(p.id)
    if hit and hit[0] > time.time():
        return hit[1]
    models: list[str] = []
    try:
        async with make_client(timeout=5) as c:
            r = await c.get(f"{p.base}/models", headers=_auth_headers(p))
            r.raise_for_status()
            data = r.json().get("data", [])
            models = sorted({m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)
                             and len(m["id"]) <= MAX_ID_CHARS})
    except Exception as e:  # provider offline (e.g. Ollama not running) - just hide it
        log.info("Could not list models for %s (%s).", p.label, e.__class__.__name__)
    _model_cache[p.id] = (time.time() + (60 if models else 15), models)
    return models


async def provider_models(p: Provider) -> list[str]:
    return p.models if p.models is not None else await discover_models(p)


def is_vision(p: Provider, model: str) -> bool:
    return model in p.vision or SETTINGS.catalog.get(model, {}).get("vision", False)


@app.get("/api/models")
async def list_models():
    s = SETTINGS
    out = []
    for p in s.providers.values():
        for m in await provider_models(p):
            info = s.catalog.get(m, {})
            out.append({
                "provider": p.id, "label": p.label, "model": m,
                "name": info.get("name") or "", "desc": info.get("desc") or "",
                "group": info.get("group") or ("" if info else "Other"),
                "rank": info.get("rank", 10 ** 6), "vision": is_vision(p, m),
            })
    return {"models": out, "groups": s.groups, "auth": bool(s.password_hash), "version": VERSION}


# =============================================================================================
# Chat
# =============================================================================================
def _sse(obj) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _friendly(status: int, err: str, label: str, model: str) -> str:
    low = err.lower()
    if status in (401, 403) or (status == 400 and "api key" in low):
        msg = f"{label} rejected the API key ({status}). Check the {label} key in .env."
    elif status == 404:
        msg = f"Model '{model}' was not found at {label} (404). Check the model ID in .env."
    elif status == 429:
        msg = f"{label} rate limit hit (429). Wait a minute and try again."
    elif status in (502, 503, 504):
        msg = f"{label} is overloaded or unavailable right now ({status}). Try again shortly or pick another model."
    elif status in (400, 413) and ("context" in low or "token" in low):
        msg = f"This chat is too long for '{model}' ({status}). Start a new chat."
    else:
        msg = f"{label} returned an error ({status})."
    # Redact BEFORE truncating, so a key can never be cut into an unrecognisable fragment.
    detail = redact(err).strip()[:300]
    return f"{msg} Details: {detail}" if detail else msg


def clean_messages(messages, vision_ok: bool, model: str) -> list:
    """Keep only role + content. Content is a string, or (user only) a list of
    OpenAI-style text / image_url parts with base64 PNG/JPEG/WEBP data URLs."""
    if not isinstance(messages, list):
        raise HTTPException(400, "'messages' must be a list")
    if len(messages) > MAX_MESSAGES:
        raise HTTPException(400, f"Too many messages in one request (max {MAX_MESSAGES}). Start a new chat.")
    clean, image_bytes = [], 0
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content", "")
        if not isinstance(content, list):
            text = content if isinstance(content, str) else str(content)
            if len(text) > MAX_TEXT_CHARS:
                raise HTTPException(413, f"A message is too long (max {MAX_TEXT_CHARS:,} characters).")
            clean.append({"role": m["role"], "content": text})
            continue
        if m["role"] != "user":
            raise HTTPException(400, "Only user messages can contain images")
        parts, n_images = [], 0
        for p in content:
            kind = p.get("type") if isinstance(p, dict) else None
            if kind == "text":
                text = str(p.get("text", ""))
                if len(text) > MAX_TEXT_CHARS:
                    raise HTTPException(413, f"A message is too long (max {MAX_TEXT_CHARS:,} characters).")
                parts.append({"type": "text", "text": text})
            elif kind == "image_url":
                url = p.get("image_url", {}).get("url", "") if isinstance(p.get("image_url"), dict) else ""
                found = DATA_URL_RE.match(url) if isinstance(url, str) else None
                if not found:
                    raise HTTPException(400, "Only PNG, JPEG or WEBP images sent as base64 data URLs are allowed")
                n_images += 1
                image_bytes += len(found.group(2)) * 3 // 4
                parts.append({"type": "image_url", "image_url": {"url": url}})
            else:
                raise HTTPException(400, f"Unsupported message part type: {str(kind)[:40]!r}")
        if n_images and not vision_ok:
            raise HTTPException(400, f"Model '{model}' can't read images. Pick a vision-capable model or remove the images.")
        if n_images > MAX_IMAGES_PER_MESSAGE:
            raise HTTPException(400, f"Too many images: max {MAX_IMAGES_PER_MESSAGE} per message")
        if parts:
            clean.append({"role": "user", "content": parts})
    if image_bytes > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"Images are too large ({image_bytes / 1048576:.1f} MB). The limit is 5 MB per message.")
    return clean


@app.post("/api/chat")
async def chat(request: Request):
    body = await _read_json(request, MAX_BODY_BYTES)
    pid, model = body.get("provider"), body.get("model")
    if not isinstance(pid, str) or not isinstance(model, str) or len(pid) > MAX_ID_CHARS or len(model) > MAX_ID_CHARS:
        raise HTTPException(400, "'provider' and 'model' must be short strings")

    s = SETTINGS
    prov = s.providers.get(pid)
    if not prov:
        raise HTTPException(400, f"Provider '{pid[:40]}' is not configured")
    if model not in await provider_models(prov):          # whitelist: only models listed / discovered
        raise HTTPException(400, f"Model '{model[:80]}' is not enabled for {prov.label}")

    clean = clean_messages(body.get("messages") or [], is_vision(prov, model), model)
    if not clean:
        raise HTTPException(400, "Nothing to send - the message is empty.")
    if s.system_prompt:
        clean.insert(0, {"role": "system", "content": s.system_prompt})

    async def stream():
        try:
            async with make_client(timeout=httpx.Timeout(120, connect=15)) as c:
                async with c.stream(
                    "POST",
                    f"{prov.base}/chat/completions",
                    headers=_auth_headers(prov),
                    json={"model": model, "messages": clean, "stream": True},
                ) as r:
                    if r.status_code != 200:
                        err = (await r.aread()).decode(errors="replace")[:4000]
                        yield _sse({"error": _friendly(r.status_code, err, prov.label, model)})
                        return
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(chunk, dict):
                            continue
                        if chunk.get("error"):
                            yield _sse({"error": f"{prov.label} error mid-reply: {redact(str(chunk['error']))[:300]}"})
                            break
                        choices = chunk.get("choices") or []
                        if not choices or not isinstance(choices[0], dict):
                            continue
                        delta = choices[0].get("delta") or {}
                        out = {}
                        if isinstance(delta.get("content"), str) and delta["content"]:
                            out["c"] = delta["content"]
                        thinking = delta.get("reasoning_content") or delta.get("reasoning")
                        if isinstance(thinking, str) and thinking:
                            out["r"] = thinking
                        if out:
                            yield _sse(out)
        except httpx.TimeoutException:
            yield _sse({"error": f"{prov.label} timed out (no data for 120 s). Try again or pick a faster model."})
        except httpx.HTTPError as e:
            yield _sse({"error": f"Lost connection to {prov.label} ({e.__class__.__name__}). "
                                 "Check the base URL in .env and your internet connection."})
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# =============================================================================================
# CLI:  python app.py               -> run on ORRERY_BIND:ORRERY_PORT (default 127.0.0.1:3000)
#       python app.py hash-password -> print an APP_PASSWORD_HASH value
# =============================================================================================
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "hash-password":
        import getpass
        first = getpass.getpass("New Orrery password (8+ characters): ")
        if len(first) < 8 or getpass.getpass("Repeat it: ") != first:
            sys.exit("Passwords were too short or did not match.")
        print("APP_PASSWORD_HASH=" + hash_password(first))
        sys.exit(0)
    import uvicorn
    uvicorn.run("app:app", host=SETTINGS.bind, port=int(os.environ.get("ORRERY_PORT", "3000")),
                access_log=False, proxy_headers=False, server_header=False)
