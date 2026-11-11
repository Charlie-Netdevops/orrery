# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's private reporting instead: go to the repository's **Security** tab and choose
**"Report a vulnerability"**. Include what you found, how to reproduce it, and which version
you're running. You'll get a reply as soon as possible. This is a one-person project, so please allow
a few days. Once a fix is released, you'll be credited in the changelog unless you'd rather not be.

## ⚠️ Never paste API keys into issues

Never include API keys, `.env` contents, passwords, or screenshots that show them in an issue, a
discussion, a pull request, or an email - not even "just a bit of the key". If you need to show
configuration, replace every secret with `REDACTED`.

If you leaked a key by accident: **revoke or rotate it immediately** in the provider's dashboard.
Deleting the issue or comment isn't enough, because it may already have been copied or indexed.

## Supported versions

Only the latest release gets security fixes.

## How Orrery protects you

- **Local only by default.** Docker publishes the port on `127.0.0.1` only. LAN mode
  (`ORRERY_BIND=0.0.0.0`) refuses to start without `APP_PASSWORD`.
- **Keys stay server-side.** API keys are read from `.env` by the server and only sent to the
  provider's base URL. They're never included in API responses, never logged, and scrubbed from
  every error message (both the exact configured values and anything key-shaped). Tests check
  this against a deliberately leaky mock provider.
- **Login.** Passwords are compared as scrypt hashes in constant time. Failed logins are
  rate-limited per client (5 per 15 minutes) and globally. Sessions use random tokens in an
  `HttpOnly`, `SameSite=Strict` cookie.
- **Browser hardening.** Strict Content-Security-Policy (`'self'` only, no inline scripts, no CDNs),
  `frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
  Model output is rendered through DOMPurify.
- **Request checks.** A Host-header allowlist blocks DNS-rebinding attacks against the localhost
  server. Cross-origin POSTs are rejected. Request size, message count, image type, image count
  and image size are all limited. Only models listed in `.env` (or discovered from the provider)
  can be used.
- **Container.** Runs as a non-root user with a read-only filesystem, all Linux capabilities
  dropped and `no-new-privileges`. The Dockerfile copies an explicit allowlist of files, and
  `.dockerignore` excludes `.env`, so secrets are never baked into the image.
- **Repository.** gitleaks runs in a pre-commit hook (`git config core.hooksPath .githooks`) and in
  GitHub Actions on every push and pull request.

## Out of scope

- Data handling by the AI providers you configure. Their terms apply to whatever you send them.
- Browser speech recognition, which is provided by your browser vendor.
- Running Orrery directly on the public internet. It isn't designed for that. If you must, put it
  behind a reverse proxy with HTTPS and proper authentication.
