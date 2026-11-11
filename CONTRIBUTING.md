# Contributing

Thanks for helping! Orrery aims to stay **small and beginner-friendly**, so please open an issue to
discuss bigger features before writing code.

1. Fork and clone, then turn on the secret-scanning hook:
   `git config core.hooksPath .githooks` (needs Docker running).
2. `pip install -r requirements-dev.txt`
3. Make your change. Keep the front end build-free (plain HTML/CSS/JS) and CSP-clean: no inline
   scripts, inline styles or CDNs.
4. Run `pytest -q` and, if you touched Docker or the server, `python tests/docker_smoke.py`.
5. Open a pull request that explains what changed and why.

**Never commit real API keys or `.env` files** - tests must use the mock provider in
`tests/mock_provider.py`. Generate any key-like test values at runtime, as the existing tests do.

By contributing, you agree that your contribution is licensed under the MIT License.
