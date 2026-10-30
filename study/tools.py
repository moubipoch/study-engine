"""Environment check: which compilers / runtimes / services are available on this machine."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import urllib.request
import json
from pathlib import Path

ANKICONNECT_URL = "http://127.0.0.1:8765"


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def _version(cmd: str) -> str | None:
    if not shutil.which(cmd):
        return None
    _, out = _run([cmd, "--version"])
    return out.splitlines()[0] if out else "present"


def _compile_and_run(src: str, flags: list[str]) -> bool:
    if not shutil.which("g++"):
        return False
    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        (dp / "t.cpp").write_text(src)
        rc, _ = _run(["g++", "-std=c++20", *flags, "t.cpp", "-o", "t"], cwd=dp)
        if rc != 0:
            return False
        rc, _ = _run([str(dp / "t")], cwd=dp)
        return rc == 0


_OMP_SRC = "#include <omp.h>\nint main(){int n=0;\n#pragma omp parallel reduction(+:n)\nn+=1;\nreturn n>0?0:1;}\n"
_THREAD_SRC = "#include <thread>\nint main(){std::thread t([]{}); t.join(); return 0;}\n"


def ankiconnect_version(url: str = ANKICONNECT_URL) -> int | None:
    try:
        req = urllib.request.Request(url, data=json.dumps({"action": "version", "version": 6}).encode())
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return json.loads(r.read())["result"]
    except Exception:
        return None


def check_all() -> dict[str, dict]:
    res: dict[str, dict] = {}

    def add(name, ok, detail=""):
        res[name] = {"ok": bool(ok), "detail": detail or ""}

    gxx = _version("g++")
    add("g++", gxx, gxx or "missing")
    add("clang++", _version("clang++"), _version("clang++") or "missing (optional)")
    add("cmake", _version("cmake"), _version("cmake") or "missing")
    add("openmp", _compile_and_run(_OMP_SRC, ["-fopenmp"]), "g++ -fopenmp")
    add("asan+ubsan", _compile_and_run(_THREAD_SRC, ["-fsanitize=address,undefined"]), "-fsanitize=address,undefined")
    add("tsan", _compile_and_run(_THREAD_SRC, ["-fsanitize=thread", "-pthread"]), "-fsanitize=thread")
    mpi = _version("mpicxx")
    add("mpi", mpi and shutil.which("mpirun"), mpi or "missing: sudo apt install openmpi-bin libopenmpi-dev")
    add("valgrind", _version("valgrind"), _version("valgrind") or "missing (optional)")
    add("perf", shutil.which("perf"), "present" if shutil.which("perf") else "missing (optional)")

    smi = shutil.which("nvidia-smi")
    gpu = ""
    if smi:
        _, gpu = _run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader"])
    add("nvidia-gpu", smi and gpu, gpu or "none")
    add("cuda-libs (gpu extra)", importlib.util.find_spec("nvidia") is not None
        and importlib.util.find_spec("nvidia.cudnn") is not None, "uv sync (default gpu group)")

    for mod, why in [("faster_whisper", "transcription"), ("fitz", "PDF (pymupdf)"),
                     ("fastembed", "embeddings"), ("yt_dlp", "YouTube"), ("tree_sitter_cpp", "C++ indexing"),
                     ("fsrs", "spaced repetition"), ("genanki", "Anki export"), ("matplotlib", "plots (sci extra)")]:
        add(f"py:{mod}", importlib.util.find_spec(mod) is not None, why)

    av = ankiconnect_version()
    add("ankiconnect", av is not None, f"v{av}" if av else "not reachable (open Anki with AnkiConnect add-on 2055492159)")
    return res
