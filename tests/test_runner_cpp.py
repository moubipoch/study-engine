import shutil

import pytest

from study.practice.runner_cpp import run_cpp

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")


def _src(tmp_path, name, code):
    p = tmp_path / name
    p.write_text(code)
    return p


def test_clean_program_runs(tmp_path):
    p = _src(tmp_path, "ok.cpp", '#include <iostream>\nint main(){ std::cout << 6*7 << "\\n"; }\n')
    r = run_cpp(p, tmp_path / "build")
    assert r.compiled and r.warnings == 0 and r.exit_code == 0 and r.stdout == "42\n" and r.sanitizer is None


def test_asan_catches_use_after_free(tmp_path):
    p = _src(tmp_path, "uaf.cpp", "#include <vector>\n#include <cstdio>\nint main(){ std::vector<int> v{1,2,3};"
                                  " int& r=v[0]; v.push_back(4); std::printf(\"%d\\n\", r); }\n")
    r = run_cpp(p, tmp_path / "build")
    assert r.compiled and r.exit_code != 0 and "heap-use-after-free" in (r.sanitizer or "")


def test_tsan_catches_race(tmp_path):
    p = _src(tmp_path, "race.cpp", "#include <thread>\nint c=0;\nint main(){ auto f=[]{for(int i=0;i<100000;++i)++c;};"
                                   " std::thread a(f), b(f); a.join(); b.join(); }\n")
    r = run_cpp(p, tmp_path / "build", profile="tsan")
    assert r.compiled and "data race" in (r.sanitizer or "")


def test_compile_error_and_warnings_and_timeout(tmp_path):
    bad = run_cpp(_src(tmp_path, "bad.cpp", "int main(){ return x; }\n"), tmp_path / "b")
    assert not bad.compiled and "x" in bad.compile_output
    warn = run_cpp(_src(tmp_path, "w.cpp", "int main(){ int unused; return 0; }\n"), tmp_path / "b")
    assert warn.compiled and warn.warnings == 1
    loop = run_cpp(_src(tmp_path, "loop.cpp", "int main(){ volatile int x=0; for(;;) ++x; }\n"), tmp_path / "b",
                   profile="plain", timeout=1)
    assert loop.timed_out
