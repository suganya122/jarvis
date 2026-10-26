"""What this computer has, and what that means for the assistant's local work.

Almost nothing heavy runs locally: answers come from Claude. What does run here is speech:
- macOS: Apple's own recognition and voices. On Apple Silicon macOS runs them on the Neural
  Engine and GPU by itself; on an Intel Mac, on the CPU. Nothing to choose.
- Linux and Windows: Vosk (speech in) and Piper or the system voice (speech out), on the CPU.
  They're small enough that a GPU wouldn't make them noticeably faster.
- An optional local model (Settings → model provider: Ollama, LM Studio) uses the GPU on its
  own: Metal on Apple Silicon, CUDA/ROCm elsewhere.
"""

import json
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Hardware:
    os: str
    chip: str
    gpu: str
    memory_gb: float
    apple_silicon: bool

    def speech(self) -> str:
        if self.os == "macOS":
            where = "the Neural Engine and GPU" if self.apple_silicon else "the CPU (Intel Mac)"
            return f"Apple's on-device speech recognition and voices, on {where}"
        return "Vosk speech recognition and Piper/system voice, on the CPU"

    def summary(self) -> str:
        gpu = f", {self.gpu}" if self.gpu and self.gpu != self.chip else ""
        return f"{self.os}: {self.chip}{gpu}, {self.memory_gb:.0f} GB memory"


def _run(*argv: str) -> str:
    if shutil.which(argv[0]) is None:
        return ""
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=8, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _memory_gb() -> float:
    from core.model_files import memory_gb

    return memory_gb()


def _mac_gpu() -> str:
    try:
        data = json.loads(_run("system_profiler", "SPDisplaysDataType", "-json") or "{}")
        cards = data.get("SPDisplaysDataType") or []
        names = [c.get("sppci_model", "") for c in cards if c.get("sppci_model")]
        cores = next((c.get("sppci_cores") for c in cards if c.get("sppci_cores")), None)
        return (names[0] + (f" ({cores}-core GPU)" if cores else "")) if names else ""
    except (ValueError, AttributeError):
        return ""


def _linux_gpu() -> str:
    if nvidia := _run("nvidia-smi", "--query-gpu=name", "--format=csv,noheader"):
        return nvidia.splitlines()[0]
    for line in _run("lspci").splitlines():
        if "VGA compatible controller" in line or "3D controller" in line:
            return line.split(":", 2)[-1].strip()
    return ""


def detect() -> Hardware:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        chip = _run("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor() or machine
        silicon = machine == "arm64" or chip.startswith("Apple")
        return Hardware("macOS", chip, _mac_gpu(), _memory_gb(), silicon)
    if sys.platform == "win32":
        return Hardware("Windows", platform.processor() or machine, "", _memory_gb(), False)
    cpu = next(
        (
            ln.split(":", 1)[1].strip()
            for ln in _read("/proc/cpuinfo").splitlines()
            if ln.startswith("model name")
        ),
        machine,
    )
    return Hardware("Linux", cpu, _linux_gpu(), _memory_gb(), False)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""
