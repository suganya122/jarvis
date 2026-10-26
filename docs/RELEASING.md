# Releasing and verifying

Releases are built by GitHub Actions on free public-repo runners, with no paid
code-signing certificates and no servers.

## Cutting a release

1. Bump `APP_VERSION` in `backend/contracts/app.py` and `version` in
   `backend/pyproject.toml` (they must match).
2. Push a tag: `git tag v0.2.0 && git push origin v0.2.0`.
3. The **Release** workflow (`.github/workflows/release.yml`):
   - builds the web UI, the wheel, and an offline bundle on Linux, macOS and Windows
     (`python packaging/build.py`);
   - **installs each bundle, starts it, checks it answers, and uninstalls it** on that OS;
   - writes `SHA256SUMS` for every file and signs it with sigstore `cosign sign-blob`
     (keyless: the signature is tied to this repository's workflow identity through
     GitHub's OIDC token, so there is no private key to store or leak);
   - creates a **draft** GitHub Release with all files. Review it, then publish.

Nothing updates itself: users download a release when they choose to (no auto-update).

## Building locally

```bash
backend/.venv/bin/python packaging/build.py            # needs npm for the UI
backend/.venv/bin/python packaging/build.py --skip-ui  # reuse frontend/dist
```

Output in `dist/`:

| File | What |
|---|---|
| `local_assistant-<ver>-py3-none-any.whl` | the app, with the web UI inside; `pipx install` it |
| `local-assistant-<ver>-<os>-<arch>.zip` | offline bundle: `install.sh` / `install.ps1`, `uninstall.*`, `wheels/` with every dependency for that OS at the versions pinned in `backend/requirements.txt` |
| `SHA256SUMS` | checksums (CI adds `SHA256SUMS.sigstore.json`) |

## Verifying a download

1. **Checksums** (detects corrupted or swapped files):

   ```bash
   sha256sum -c SHA256SUMS --ignore-missing          # Linux
   shasum -a 256 -c SHA256SUMS --ignore-missing      # macOS
   ```

   Windows (PowerShell): `Get-FileHash .\local-assistant-*.zip` and compare with the
   line in `SHA256SUMS`.

2. **Signature** (proves `SHA256SUMS` came from this repository's release workflow).
   Install [cosign](https://github.com/sigstore/cosign) (free, Apache-2.0), then:

   ```bash
   cosign verify-blob SHA256SUMS \
     --bundle SHA256SUMS.sigstore.json \
     --certificate-oidc-issuer https://token.actions.githubusercontent.com \
     --certificate-identity-regexp '^https://github.com/<owner>/<repo>/\.github/workflows/release\.yml@refs/tags/v'
   ```

   Replace `<owner>/<repo>` with the repository you downloaded from. "Verified OK" plus
   matching checksums means the files are exactly what the workflow built from that
   tag.

## Why not PyInstaller / signed installers

- Code-signing certificates (Apple Developer ID, Windows OV/EV) cost money every year,
  which this project rules out.
- Unsigned frozen executables are exactly what macOS Gatekeeper and Windows SmartScreen
  warn about. A bundle of wheels plus a script the user runs avoids those prompts
  without asking anyone to bypass a security feature.
- PyInstaller is GPL-2.0 with a bootloader exception. That is allowed, but the brief asks
  for permissive licenses only, so it isn't used.
