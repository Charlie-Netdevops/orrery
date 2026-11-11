<p align="center">
  <img src="static/logo.svg" width="96" height="96" alt="Orrery logo: a small sun with planets on thin orbit rings">
</p>

<h1 align="center">Orrery</h1>
<p align="center"><b>Your models, in orbit.</b></p>
<p align="center">A lightweight, one-container, self-hosted AI chat you can set up in minutes.<br>
Free-model presets, voice and vision built in, private by default.</p>

---

Orrery is a small, friendly chat app that runs on your own computer and talks to the AI
providers *you* choose. You bring an API key (many providers have a free tier); Orrery gives you a
clean chat window with a model picker, image upload, and voice. One Docker container, no database,
no account, nothing to sign up for.

> 📸 **Screenshot placeholder** - `docs/screenshot-chat.png` (chat with the model picker open)
>
> 🎞️ **GIF placeholder** - `docs/demo.gif` (voice mode: ask a question, hear the answer)

## Why Orrery

- **Simple on purpose.** One container, one `.env` file, one page. If you can copy a file and run
  one command, you can run Orrery.
- **Start free.** Point it at a provider with a free tier and you're chatting. Switch models per
  message to get a second opinion.
- **Private by default.** It only listens on your own computer (`127.0.0.1`). Your chats are stored
  in your browser, not on a server. Your API keys never leave the server side.
- **Nice to use.** Dark theme, keyboard friendly, works on phones, and readable answers with code
  blocks, tables and copy buttons.

## Features

- **Any OpenAI-compatible provider.** Works with OpenAI-compatible APIs such as NVIDIA, Gemini,
  OpenRouter, Groq, OpenAI and a local Ollama - or any other endpoint that speaks the same API.
- **Model picker with friendly names** and a "best for" line under each model, grouped
  (Everyday, Coding, Reasoning, Vision) and searchable.
- **Streaming replies**, including a collapsible "Thinking" section for reasoning models.
- **Vision:** attach up to 3 images per message by pasting, drag-and-drop, or the paperclip.
  Images are resized in your browser before sending.
- **Voice:** dictate with the mic, have any reply read aloud, or turn on **voice mode** for a
  hands-free conversation (with an echo filter so it doesn't hear itself).
- **Chat history** in your browser, regenerate, stop, copy.
- **Clear error messages** ("the key was rejected", "rate limit - wait a minute") instead of raw
  stack traces.
- **Optional password + LAN mode** to use it from your phone on your home network.

## Quick start

You need [Docker](https://docs.docker.com/get-docker/) (Docker Desktop on Windows/macOS) and one API key.

```bash
git clone https://github.com/Charlie-Netdevops/orrery.git && cd orrery
cp .env.example .env        # then open .env and paste one API key (e.g. NVIDIA_API_KEY=...)
docker compose up -d --build
```

Open **http://127.0.0.1:3000** and pick a model. That's it.

> Windows PowerShell: use `Copy-Item .env.example .env` instead of `cp`.

To stop it: `docker compose down`. To update: `git pull && docker compose up -d --build`.

## Configuration

Everything lives in `.env` (start from [.env.example](.env.example), which is fully commented).

**Providers.** List the ones you want, then give each a base URL, a key and its models:

```ini
ORRERY_PROVIDERS=nvidia,ollama

NVIDIA_LABEL=NVIDIA
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_API_KEY=            # paste your key here
NVIDIA_MODELS=deepseek-ai/deepseek-v4.1-flash,openai/gpt-oss-20b

OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
OLLAMA_NO_KEY=true
OLLAMA_MODELS=             # empty = detect installed models automatically
```

A provider without a key is skipped (unless `<ID>_NO_KEY=true`), so you can leave the examples in
place and fill in only what you use. Add any other OpenAI-compatible service the same way: put a
new id in `ORRERY_PROVIDERS` and add `<ID>_BASE_URL`, `<ID>_API_KEY`, `<ID>_MODELS`.

**Model names and "best for" lines** come from [config/models.json](config/models.json). Edit it to
rename models, change groups, or mark a model as able to read images (`"vision": true`).
Changes apply after `docker compose restart`.

**Other settings**

| Setting | Default | What it does |
|---|---|---|
| `ORRERY_PORT` | `3000` | Port on your computer |
| `ORRERY_BIND` | `127.0.0.1` | `127.0.0.1` = only this computer. `0.0.0.0` = LAN mode (needs `APP_PASSWORD`) |
| `APP_PASSWORD` | empty | Adds a login page. Required in LAN mode. 8+ characters |
| `APP_PASSWORD_HASH` | empty | Store a hash instead of the password (`docker compose run --rm orrery python app.py hash-password`) |
| `ORRERY_ALLOWED_HOSTS` | empty | Extra host names you open Orrery under, e.g. `orrery.home.lan` |
| `SYSTEM_PROMPT` | empty | Instructions added to every chat |

### Using it from your phone (LAN mode)

1. In `.env` set `ORRERY_BIND=0.0.0.0` **and** a strong `APP_PASSWORD`.
2. `docker compose up -d`
3. On your phone, open `http://<your-computer's-IP>:3000` and log in.

> ⚠️ LAN mode makes Orrery reachable by every device on your network. Orrery refuses to start in
> LAN mode without a password, and it limits failed logins - but only use it on networks you trust,
> never on public Wi-Fi. Voice input in the browser generally needs HTTPS or `localhost`, so on a
> phone over plain HTTP the microphone may be unavailable.

## Privacy

Please read this - it matters more than any feature.

- **Your chats go to the provider you choose.** When you send a message, Orrery forwards it (and any
  attached images) to the provider of the selected model. That provider's terms and privacy policy
  apply. Local models via Ollama never leave your computer.
- **Free tiers are often trial or prototyping services.** Some free offerings may log, review, or
  use your prompts to improve their models. Check each provider's terms, and don't send personal,
  confidential, or work data to a service you haven't vetted.
- **Bring your own API keys.** Orrery doesn't come with keys and doesn't resell access.
- **Keys stay on your machine.** Keys live in `.env` on your computer and are only used by the
  Orrery server to call the provider. They are never sent to the browser, never written to logs,
  and scrubbed from error messages. `.env` is git-ignored and Docker-ignored.
- **Chat history stays in your browser** (localStorage). Clearing site data deletes it. Full-size
  images are only kept in memory for sending; saved chats keep small thumbnails.
- **Voice input uses your browser's speech service.** In Chrome and Edge, speech recognition may
  send audio to Google or Microsoft to transcribe it. Orrery shows a one-time notice before using
  the mic. "Read aloud" uses your system's voices.
- **No telemetry.** Orrery doesn't phone home, has no analytics, and loads nothing from CDNs.

## Troubleshooting

| Problem | Fix |
|---|---|
| Picker says "No models" | No provider has a key. Fill in a key in `.env`, then `docker compose up -d` |
| "rejected the API key (401)" | Key typo, expired or revoked key, or it belongs to a different provider |
| "rate limit hit (429)" | Free tiers have limits. Wait a minute or switch models |
| "Model ... was not found (404)" | The model ID in `.env` is wrong or retired. Copy the exact ID from the provider's site |
| Ollama models don't show | Ollama isn't running, or it isn't reachable: check `OLLAMA_BASE_URL` |
| Page shows "Host not allowed" | You opened it under a new name. Add it to `ORRERY_ALLOWED_HOSTS` |
| Container keeps restarting | `docker logs orrery` - usually LAN mode without `APP_PASSWORD` |
| Port 3000 already in use | Set `ORRERY_PORT=3001` (or any free port) in `.env` |
| Mic button missing | Voice input needs Chrome or Edge. Firefox doesn't support speech recognition |
| Mic "blocked" | Allow the microphone for the site in the browser's address bar |

Still stuck? `docker logs orrery` shows what the server saw (keys are redacted there too).

## FAQ

**Do I have to pay for AI?** No. Several providers offer free tiers or free models, and Ollama runs
models on your own computer for free. Paid APIs work too.

**Do I need a GPU?** No - cloud providers do the heavy lifting. Only Ollama runs models locally.

**Is it multi-user?** No. Orrery is built for one person (or a household sharing a password).
Chats are stored per browser.

**Can I use Claude, Mistral, DeepSeek, ...?** Any model reachable through an OpenAI-compatible
endpoint works - directly, or via a router service such as OpenRouter.

**Where is my data?** Chats: in your browser. Keys: in `.env` on your computer. Messages: sent to the
provider you picked.

## Roadmap

Ideas, not promises:

- Prebuilt image on GHCR (no local build)
- Export / import chats
- Per-chat system prompts
- Light theme
- More model presets

## Orrery vs bigger platforms

Projects like Open WebUI and LibreChat are excellent and do far more: multiple users, accounts,
document search (RAG), plugins, a database, admin panels. If you need those, use them.

Orrery is deliberately smaller: a single container with no database and a short, readable codebase.
It's for people who want a good-looking, private chat window for their own API keys and want it
running in five minutes, not for teams.

## Get the one-click installer kit

Prefer not to touch a terminal? The **Orrery installer kit** sets everything up for you on Windows,
macOS and Linux: it checks Docker, asks for your key (hidden input), writes a locked-down `.env`,
picks a free port, starts Orrery and opens your browser. It also includes model presets and an
illustrated setup guide for beginners.

👉 **[Get the installer kit](<GUMROAD_URL>)** - buying it supports development. Orrery itself stays
free and open source.

## Security

See [SECURITY.md](SECURITY.md). In short: binds to `127.0.0.1` by default, strict
Content-Security-Policy, keys never reach the browser, login rate limiting, and gitleaks secret
scanning on every commit (pre-commit hook) and every push (GitHub Actions).

Turn on the pre-commit hook in your clone:

```bash
git config core.hooksPath .githooks
```

It runs gitleaks in Docker on your staged changes and blocks the commit if it finds a secret.

## Development

```bash
pip install -r requirements-dev.txt
pytest -q                          # unit tests (mock provider, no network, no real keys)
python tests/docker_smoke.py       # build + run in Docker on 127.0.0.1:3200, then clean up
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

Built by Yash Patel with AI assistance (AI pair-programming for code, tests and docs, reviewed by a
human). Uses [marked](https://github.com/markedjs/marked) and
[DOMPurify](https://github.com/cure53/DOMPurify) - see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

[MIT](LICENSE) © 2026 Yash Patel
