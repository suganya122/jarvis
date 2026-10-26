"""Build a release for the current OS: the app wheel (with the web UI inside), and an
offline install bundle with every dependency wheel for this OS, plus SHA256SUMS.

    python packaging/build.py            # builds the web UI too (needs npm)
    python packaging/build.py --skip-ui  # reuse frontend/dist

Output in dist/:
    local_assistant-<ver>-py3-none-any.whl
    local-assistant-<ver>-<os>-<arch>.zip   (install / uninstall scripts + wheels/)
    SHA256SUMS

No paid code signing: CI signs SHA256SUMS with sigstore (cosign, keyless); see
docs/RELEASING.md for how users verify a download.
"""

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
WEBUI = BACKEND / "core" / "webui"
DIST = ROOT / "dist"
SCRIPTS = Path(__file__).resolve().parent / "scripts"


def run(*cmd: str, cwd: Path = ROOT) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(  # noqa: S603 - our own build commands
        cmd, cwd=cwd, check=True, shell=sys.platform == "win32" and cmd[0] == "npm"
    )


def project() -> tuple[str, str]:
    meta = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return meta["name"], meta["version"]


def os_tag() -> str:
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower().replace("amd64", "x86_64").replace("aarch64", "arm64")
    return f"{system}-{machine}"


def build_ui(skip: bool) -> None:
    if not skip:
        run("npm", "ci", cwd=FRONTEND)
        run("npm", "run", "build", cwd=FRONTEND)
    if not (FRONTEND / "dist" / "index.html").exists():
        raise SystemExit("frontend/dist is missing: build the UI first (npm run build)")
    shutil.rmtree(WEBUI, ignore_errors=True)
    shutil.copytree(FRONTEND / "dist", WEBUI)


def clean_backend() -> None:
    for leftover in (BACKEND / "build", *BACKEND.glob("*.egg-info")):
        shutil.rmtree(leftover, ignore_errors=True)


def build_wheel(out: Path) -> Path:
    clean_backend()
    run(
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        "--wheel-dir",
        str(out),
        str(BACKEND),
    )
    clean_backend()
    name, version = project()
    [wheel] = out.glob(f"{name.replace('-', '_')}-{version}-*.whl")
    return wheel


def build_bundle(wheel: Path) -> Path:
    name, version = project()
    folder = DIST / f"{name}-{version}-{os_tag()}"
    shutil.rmtree(folder, ignore_errors=True)
    wheels = folder / "wheels"
    wheels.mkdir(parents=True)
    shutil.copy2(wheel, wheels)
    # Every dependency for this OS, at the pinned versions, so installing needs no internet.
    run(sys.executable, "-m", "pip", "wheel", "--wheel-dir", str(wheels),
        "--constraint", str(BACKEND / "requirements.txt"), str(wheel))  # fmt: skip
    for script in SCRIPTS.iterdir():
        if script.suffix == (".ps1" if sys.platform == "win32" else ".sh") or script.suffix == ".txt":
            shutil.copy2(script, folder / script.name)
    for doc in ("LICENSE", "THIRD_PARTY.md"):
        shutil.copy2(ROOT / doc, folder / doc)
    # The libraries in wheels/ are built for this Python (e.g. 3.12): the install scripts use it.
    (folder / "python-version.txt").write_text(f"{sys.version_info.major}.{sys.version_info.minor}\n")
    archive = shutil.make_archive(str(folder), "zip", root_dir=DIST, base_dir=folder.name)
    shutil.rmtree(folder)
    return Path(archive)


def checksums(files: list[Path]) -> Path:
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in sorted(files)]
    out = DIST / "SHA256SUMS"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    formatter = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=formatter)
    parser.add_argument("--skip-ui", action="store_true", help="use the existing frontend/dist")
    args = parser.parse_args()
    DIST.mkdir(exist_ok=True)
    build_ui(args.skip_ui)
    try:
        wheel = build_wheel(DIST)
    finally:
        shutil.rmtree(WEBUI, ignore_errors=True)  # only the wheel needs this copy
    bundle = build_bundle(wheel)
    sums = checksums([wheel, bundle])
    print(f"\nBuilt:\n  {wheel}\n  {bundle}\n  {sums}")


if __name__ == "__main__":
    main()
