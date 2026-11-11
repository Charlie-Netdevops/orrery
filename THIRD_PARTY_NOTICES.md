# Third-party notices

Orrery itself is MIT-licensed (see [LICENSE](LICENSE)). It ships with, or installs, the
following third-party software. Each component stays under its own license.

## Bundled in this repository (front end, served locally - no CDN)

| Component | Version | License | Files |
|---|---|---|---|
| [marked](https://github.com/markedjs/marked) | 12.0.2 | MIT | `static/vendor/marked.min.js`, license: `static/vendor/marked.LICENSE.md` |
| [DOMPurify](https://github.com/cure53/DOMPurify) | 3.1.6 | Apache-2.0 OR MPL-2.0 | `static/vendor/purify.min.js`, license: `static/vendor/dompurify.LICENSE` |

Both files were taken unmodified from the official npm packages (`npm pack`, integrity-checked).

## Installed into the Docker image (Python packages)

Direct dependencies are pinned in `requirements.txt`; the versions of indirect dependencies
below are the ones resolved for the v1.0.0 release and may differ in later builds.

| Package | Version | License | Why |
|---|---|---|---|
| fastapi | 0.115.6 | MIT | web framework (direct) |
| uvicorn[standard] | 0.34.0 | BSD-3-Clause | web server (direct) |
| httpx | 0.28.1 | BSD-3-Clause | calls the AI providers (direct) |
| starlette | 0.41.3 | BSD-3-Clause | used by fastapi |
| pydantic / pydantic-core | 2.x | MIT | used by fastapi |
| annotated-types | 0.8.x | MIT | used by pydantic |
| typing-extensions | 4.x | PSF-2.0 | used by pydantic |
| typing-inspection | 0.4.x | MIT | used by pydantic |
| anyio | 4.x | MIT | async I/O |
| httpcore | 1.0.x | BSD-3-Clause | used by httpx |
| h11 | 0.16.x | MIT | HTTP/1.1 |
| idna | 3.x | BSD-3-Clause | used by httpx |
| certifi | 2026.x | MPL-2.0 | TLS root certificates |
| click | 8.x | BSD-3-Clause | used by uvicorn |
| httptools | 0.9.x | MIT | uvicorn[standard] |
| uvloop (Linux/macOS) | 0.2x | MIT OR Apache-2.0 | uvicorn[standard] |
| websockets | 17.x | BSD-3-Clause | uvicorn[standard] |
| watchfiles | 1.x | MIT | uvicorn[standard] |
| python-dotenv | 1.x | BSD-3-Clause | uvicorn[standard] |
| PyYAML | 6.x | MIT | uvicorn[standard] |
| colorama (Windows only) | 0.4.x | BSD-3-Clause | used by click |

## Base image

The Docker image is built `FROM python:3.12-slim` (the Docker Official Image), which contains
CPython (PSF-2.0) and Debian packages under their respective licenses.

## Development only (not shipped)

| Tool | License | Use |
|---|---|---|
| pytest | MIT | tests (`requirements-dev.txt`) |
| gitleaks (Docker image `ghcr.io/gitleaks/gitleaks`) | MIT | pre-commit hook and CI secret scanning |
