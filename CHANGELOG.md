# Changelog

All notable changes to Orrery are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-10

First public release.

### Added
- Chat with any OpenAI-compatible provider configured in `.env` (examples for NVIDIA, Gemini,
  OpenRouter, Groq, OpenAI and local Ollama); model auto-detection when no list is given.
- Model picker with friendly names, "best for" descriptions, groups and search
  (`config/models.json`).
- Streaming replies with a collapsible reasoning ("Thinking") section.
- Vision: up to 3 images per message via paste, drag-and-drop or the paperclip, resized in the browser.
- Voice: dictation, read aloud, and hands-free voice mode with echo filtering and barge-in.
- Chat history in the browser, regenerate, stop, copy.
- Optional password login (scrypt, rate-limited) and LAN mode that requires it.
- Security headers (strict CSP), Host-header allowlist, cross-origin POST blocking, key redaction
  in errors and logs, non-root read-only container.
- Front-end libraries vendored locally (no CDN).
- gitleaks pre-commit hook and GitHub Actions secret scanning; unit tests and a Docker smoke test
  using a mock provider.
