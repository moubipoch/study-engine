"""Run Python exercises (numpy/scipy/matplotlib available) with a timeout, and pytest-based grading."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MAX_OUT = 20000


def _trim(s: str) -> str:
    return s if len(s) <= MAX_OUT else s[:MAX_OUT // 2] + "\n…[truncated]…\n" + s[-MAX_OUT // 2:]


def run_py(script: Path, timeout: float = 60, args: list[str] | None = None) -> dict:
    env = os.environ | {"MPLBACKEND": "Agg", "PYTHONHASHSEED": "0"}
    try:
        p = subprocess.run([sys.executable, str(script), *(args or [])], capture_output=True, text=True,
                           timeout=timeout, cwd=script.parent, env=env)
        return {"exit_code": p.returncode, "stdout": _trim(p.stdout), "stderr": _trim(p.stderr), "timed_out": False}
    except subprocess.TimeoutExpired as e:
        return {"exit_code": None, "stdout": _trim(e.stdout or "") if isinstance(e.stdout, str) else "",
                "stderr": "", "timed_out": True}


def run_pytest(workdir: Path, timeout: float = 120) -> dict:
    """Run pytest in a folder that holds the answer (solution.py …) and the tests (test_*.py)."""
    env = os.environ | {"MPLBACKEND": "Agg", "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short"],
                           capture_output=True, text=True, timeout=timeout, cwd=workdir, env=env)
        out = p.stdout + p.stderr
        last = out.strip().splitlines()[-1] if out.strip() else ""
        return {"passed": p.returncode == 0, "exit_code": p.returncode, "summary": last, "output": _trim(out),
                "timed_out": False, "compiled": True}
    except subprocess.TimeoutExpired:
        return {"passed": False, "exit_code": None, "summary": "timed out", "output": "", "timed_out": True,
                "compiled": True}
