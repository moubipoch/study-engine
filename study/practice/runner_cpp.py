"""Compile and run C++ safely-ish: separate build dir, sanitizers, CPU/time limits, captured output.

Profiles:
  check  -std=c++20 -Wall -Wextra -g -fsanitize=address,undefined   (default: catches UB and memory bugs)
  tsan   -std=c++20 -Wall -Wextra -g -O1 -fsanitize=thread -pthread  (data races)
  bench  -std=c++20 -Wall -Wextra -O3 -march=native -DNDEBUG          (timing; repeat and take the median)
  plain  -std=c++20 -Wall -Wextra -g -O0
"""

from __future__ import annotations

import hashlib
import os
import resource
import shutil
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROFILES = {
    "check": ["-std=c++20", "-Wall", "-Wextra", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"],
    "tsan": ["-std=c++20", "-Wall", "-Wextra", "-g", "-O1", "-fsanitize=thread", "-pthread"],
    "bench": ["-std=c++20", "-Wall", "-Wextra", "-O3", "-march=native", "-DNDEBUG"],
    "plain": ["-std=c++20", "-Wall", "-Wextra", "-g", "-O0"],
}
CPP_SOURCES = {".cpp", ".cc", ".cxx", ".c++"}
MAX_OUT = 20000


@dataclass
class RunResult:
    profile: str
    compiled: bool
    compile_output: str = ""
    warnings: int = 0
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    sanitizer: str | None = None  # "AddressSanitizer: heap-use-after-free", ...
    seconds: float | None = None
    runs: list[float] = field(default_factory=list)
    binary: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _sources(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("*")
                  if p.suffix in CPP_SOURCES and "build" not in p.relative_to(target).parts)


def _limit_child(cpu_seconds: int):
    def fn():
        # CPU time is summed over threads, so allow every core to be busy for the whole wall timeout
        limit = cpu_seconds * (os.cpu_count() or 1)
        resource.setrlimit(resource.RLIMIT_CPU, (limit, limit + 1))
        os.setsid()
    return fn


def _trim(s: str) -> str:
    return s if len(s) <= MAX_OUT else s[:MAX_OUT // 2] + "\n…[truncated]…\n" + s[-MAX_OUT // 2:]


def _sanitizer_summary(stderr: str) -> str | None:
    for line in stderr.splitlines():
        if line.startswith("SUMMARY:"):
            return line[len("SUMMARY:"):].strip()[:200]
    for line in stderr.splitlines():
        for tag in ("AddressSanitizer", "LeakSanitizer", "ThreadSanitizer", "UndefinedBehaviorSanitizer", "runtime error:"):
            if tag in line:
                return line.strip()[:200]
    return None


def compile_cpp(target: Path, build_root: Path, profile: str = "check", extra_flags: list[str] | None = None,
                links: list[str] | None = None) -> RunResult:
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {', '.join(PROFILES)}")
    gxx = shutil.which("g++")
    if not gxx:
        return RunResult(profile, False, "g++ not found")
    srcs = _sources(target)
    if not srcs:
        return RunResult(profile, False, f"no C++ sources in {target}")
    key = hashlib.sha256("|".join([profile, *(extra_flags or []), *(str(s) + s.read_text(errors='replace')
                                                                    for s in srcs)]).encode()).hexdigest()[:16]
    out_dir = build_root / key
    out_dir.mkdir(parents=True, exist_ok=True)
    binary = out_dir / "a.out"
    incs = sorted({f"-I{s.parent}" for s in srcs})
    cmd = [gxx, *PROFILES[profile], *(extra_flags or []), *incs, *map(str, srcs), "-o", str(binary), *(links or [])]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    out = p.stdout + p.stderr
    return RunResult(profile, p.returncode == 0, _trim(out), out.count("warning:"), binary=str(binary) if p.returncode == 0 else None)


def run_binary(res: RunResult, *, args: list[str] | None = None, stdin: str = "", timeout: float = 10.0,
               repeat: int = 1, env: dict | None = None) -> RunResult:
    if not res.compiled or not res.binary:
        return res
    run_env = os.environ | {"ASAN_OPTIONS": "detect_leaks=1:abort_on_error=0", "UBSAN_OPTIONS": "print_stacktrace=1"} | (env or {})
    for i in range(max(1, repeat)):
        t0 = time.perf_counter()
        try:
            p = subprocess.run([res.binary, *(args or [])], input=stdin, capture_output=True, text=True,
                               timeout=timeout, preexec_fn=_limit_child(int(timeout) + 1), env=run_env,
                               cwd=Path(res.binary).parent)
        except subprocess.TimeoutExpired as e:
            res.timed_out = True
            res.stdout = _trim((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))
            return res
        res.runs.append(time.perf_counter() - t0)
        if i == 0:
            res.exit_code, res.stdout, res.stderr = p.returncode, _trim(p.stdout), _trim(p.stderr)
            res.sanitizer = _sanitizer_summary(p.stderr)
            if p.returncode != 0:
                break
    res.seconds = statistics.median(res.runs) if res.runs else None
    return res


def run_cpp(target: Path, build_root: Path, *, profile: str = "check", args=None, stdin: str = "", timeout: float = 10.0,
            repeat: int = 1, extra_flags=None, links=None, compile_only: bool = False) -> RunResult:
    res = compile_cpp(target, build_root, profile, extra_flags, links)
    if compile_only:
        return res
    return run_binary(res, args=args, stdin=stdin, timeout=timeout, repeat=repeat)
