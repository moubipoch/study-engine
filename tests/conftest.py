import pymupdf
import pytest

from study import config
from study.db import connect

CPP = """#include <vector>
#include <iostream>

namespace sim {
template <typename T>
T sum(const std::vector<T>& v) {
    T s{};
    for (auto x : v) s += x;
    return s;
}

class Particle {
public:
    double x, v;
    void step(double dt) { x += v * dt; }
};
}  // namespace sim

int main() {
    std::vector<double> v{1, 2, 3};
    std::cout << sim::sum(v) << "\\n";
}
"""


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "courses" / "demo").mkdir(parents=True)
    (root / "pyproject.toml").write_text("")
    (root / "courses" / "demo" / "course.yaml").write_text("name: Demo\nexam_date: 2027-02-01\n")
    paths = config.Paths(root)
    course = config.get_course(paths, "demo")
    con = connect(paths.db)
    return paths, course, con


@pytest.fixture
def materials(tmp_path):
    m = tmp_path / "materials"
    m.mkdir()
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"Lecture 3 slide {i + 1}\nMove semantics and rvalue references, page {i + 1}.")
    doc.save(m / "lecture03_slides.pdf")
    ex = m / "ex02"
    ex.mkdir()
    (ex / "vector.cpp").write_text(CPP)
    (ex / "notes.md").write_text("# Ownership\nunique_ptr owns.\n\n# Moves\nstd::move is a cast.\n")
    (m / "L03.srt").write_text(
        "1\n00:00:00,000 --> 00:00:40,000\nToday we talk about move semantics.\n\n"
        "2\n00:00:40,000 --> 00:01:30,000\nAn rvalue reference binds to temporaries.\n\n"
        "3\n00:01:30,000 --> 00:02:10,000\nstd move is just a cast.\n")
    return m
