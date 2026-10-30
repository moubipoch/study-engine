"""Geometric intuition for dynamical systems (NLD, MPC, DPOC): phase portraits and bifurcation diagrams.

Systems are given as plain expressions, e.g.  "y; -sin(x) - 0.2*y"  for x' = y, y' = -sin x - 0.2 y.
Expressions are parsed with a whitelist (numbers, x, y, the parameter, + - * / **, numpy functions):
nothing else can be executed.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

_FUNCS = {n: getattr(np, n) for n in ("sin", "cos", "tan", "exp", "log", "sqrt", "tanh", "sinh", "cosh", "arctan",
                                      "abs", "sign")}
_CONSTS = {"pi": np.pi, "e": np.e}
_OK = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, ast.Call, ast.Add, ast.Sub,
       ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd, ast.Mod)


class ExprError(ValueError):
    pass


def compile_expr(src: str, variables: tuple[str, ...]):
    src = src.replace("^", "**").strip()
    tree = ast.parse(src, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _OK):
            raise ExprError(f"not allowed in an expression: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in (*variables, *_FUNCS, *_CONSTS):
            raise ExprError(f"unknown name {node.id!r} (allowed: {', '.join(variables)}, {', '.join(_FUNCS)})")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS):
            raise ExprError("only numpy functions like sin, exp may be called")
    code = compile(tree, "<expr>", "eval")
    env = {"__builtins__": {}, **_FUNCS, **_CONSTS}
    return lambda **vals: eval(code, env, vals)  # noqa: S307 (whitelisted AST above)


@dataclass
class FixedPoint:
    x: float
    y: float
    eigenvalues: list
    kind: str


def classify(J: np.ndarray, tol: float = 1e-7) -> tuple[str, list]:
    ev = np.linalg.eigvals(J)
    re, im = ev.real, ev.imag
    tr, det = np.trace(J), np.linalg.det(J)
    if abs(det) < tol:
        kind = "non-isolated / degenerate (det J = 0): linearization inconclusive"
    elif det < 0:
        kind = "saddle"
    elif np.all(np.abs(re) < tol):
        kind = "center (linear): non-hyperbolic, so nonlinear terms decide"
    elif np.any(np.abs(im) > tol):
        kind = "stable spiral (focus)" if tr < 0 else "unstable spiral (focus)"
    else:
        kind = "stable node" if tr < 0 else "unstable node"
        if abs(tr * tr - 4 * det) < 1e-6:
            kind += " (degenerate/star)"
    return kind, [complex(round(v.real, 6), round(v.imag, 6)) for v in ev]


def phase_portrait(fx: str, fy: str, xlim=(-3, 3), ylim=(-3, 3), out: Path | None = None, title: str = "",
                   trajectories: int = 8) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy.integrate import solve_ivp
    from scipy.optimize import fsolve
    f, g = compile_expr(fx, ("x", "y")), compile_expr(fy, ("x", "y"))

    def F(v):
        return np.array([f(x=v[0], y=v[1]), g(x=v[0], y=v[1])], dtype=float)

    def jac(v, h=1e-6):
        J = np.zeros((2, 2))
        for j in range(2):
            d = np.zeros(2)
            d[j] = h
            J[:, j] = (F(v + d) - F(v - d)) / (2 * h)
        return J
    # fixed points: Newton from a grid of seeds, deduplicated
    fps: list[FixedPoint] = []
    for sx in np.linspace(*xlim, 15):
        for sy in np.linspace(*ylim, 15):
            sol, info, ier, _ = fsolve(F, [sx, sy], full_output=True)
            if ier != 1 or np.linalg.norm(F(sol)) > 1e-8:
                continue
            if not (xlim[0] - 1e-9 <= sol[0] <= xlim[1] + 1e-9 and ylim[0] - 1e-9 <= sol[1] <= ylim[1] + 1e-9):
                continue
            if any(np.hypot(sol[0] - p.x, sol[1] - p.y) < 1e-4 for p in fps):
                continue
            kind, ev = classify(jac(sol))
            fps.append(FixedPoint(round(float(sol[0]), 6), round(float(sol[1]), 6), ev, kind))
    if out:
        X, Y = np.meshgrid(np.linspace(*xlim, 40), np.linspace(*ylim, 40))
        U, V = f(x=X, y=Y) * np.ones_like(X), g(x=X, y=Y) * np.ones_like(X)
        fig, ax = plt.subplots(figsize=(7, 6))
        ax.streamplot(X, Y, U, V, density=1.3, color=np.hypot(U, V), cmap="Blues", linewidth=0.8)
        ax.contour(X, Y, U, levels=[0], colors="tab:orange", linewidths=1.2)  # x-nullcline
        ax.contour(X, Y, V, levels=[0], colors="tab:green", linewidths=1.2)   # y-nullcline
        rng = np.random.default_rng(0)
        for x0 in rng.uniform([xlim[0], ylim[0]], [xlim[1], ylim[1]], size=(trajectories, 2)):
            s = solve_ivp(lambda t, v: F(v), (0, 15), x0, max_step=0.05)
            ax.plot(s.y[0], s.y[1], color="0.35", lw=0.9)
        styles = {"saddle": ("X", "tab:purple"), "stable": ("o", "black"), "unstable": ("o", "white"),
                  "center": ("D", "tab:red")}
        for p in fps:
            key = "saddle" if "saddle" in p.kind else "center" if "center" in p.kind else (
                "stable" if p.kind.startswith("stable") else "unstable")
            m, col = styles[key]
            ax.plot(p.x, p.y, m, ms=10, mfc=col, mec="black", zorder=5)
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_xlabel("x")
        ax.set_ylabel("y")
        ax.set_title(title or f"x' = {fx},  y' = {fy}\n(orange: x-nullcline, green: y-nullcline)")
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=130, bbox_inches="tight")
        plt.close(fig)
    return {"fixed_points": [asdict(p) | {"eigenvalues": [str(e) for e in p.eigenvalues]} for p in fps],
            "image": str(out) if out else None}


def bifurcation_1d(f: str, param: str = "r", prange=(-2, 2), xlim=(-3, 3), out: Path | None = None,
                   title: str = "") -> dict:
    """Equilibria of x' = f(x; r) versus r: solid = stable (f_x < 0), dashed = unstable."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fn = compile_expr(f, ("x", param))
    rs = np.linspace(*prange, 400)
    xs = np.linspace(*xlim, 4001)
    stable, unstable = [], []
    for r in rs:
        vals = fn(x=xs, **{param: r}) * np.ones_like(xs)
        idx = np.where(np.sign(vals[:-1]) * np.sign(vals[1:]) < 0)[0]
        exact = np.where(vals == 0)[0]
        for i in sorted(set(idx) | set(exact)):
            x0 = xs[i] if vals[i] == 0 else xs[i] - vals[i] * (xs[i + 1] - xs[i]) / (vals[i + 1] - vals[i])
            h = 1e-5
            slope = (fn(x=x0 + h, **{param: r}) - fn(x=x0 - h, **{param: r})) / (2 * h)
            (stable if slope < 0 else unstable).append((r, x0))
    # where the number of equilibria changes = candidate bifurcation points
    counts = []
    for r in rs:
        vals = fn(x=xs, **{param: r}) * np.ones_like(xs)
        counts.append(int(np.sum(np.sign(vals[:-1]) * np.sign(vals[1:]) < 0)))
    changes = [round(float((rs[i] + rs[i + 1]) / 2), 3) for i in range(len(rs) - 1) if counts[i] != counts[i + 1]]
    if out:
        fig, ax = plt.subplots(figsize=(7, 5))
        if stable:
            ax.plot(*zip(*stable), ".", ms=1.5, color="black", label="stable")
        if unstable:
            ax.plot(*zip(*unstable), ".", ms=1.0, color="tab:red", label="unstable")
        for c in changes:
            ax.axvline(c, color="0.7", ls=":", lw=1)
        ax.set_xlabel(param)
        ax.set_ylabel("x*")
        ax.set_title(title or f"x' = {f}")
        ax.legend(markerscale=6)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=130, bbox_inches="tight")
        plt.close(fig)
    return {"bifurcation_points": changes, "image": str(out) if out else None,
            "n_stable": len(stable), "n_unstable": len(unstable)}
