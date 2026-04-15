"""R11+ competition submission validator.

Enforces the pluggability contract defined in ``competition/COMPETITION.md``:
strategies must be NautilusTrader ``Strategy`` subclasses with a module-level
``MANIFEST`` and must satisfy a set of hard live-trading safety constraints
that cannot be checked by the Python type system alone (``print()`` calls,
``round()`` on price variables, margin/futures keywords, etc.).

Usage::

    python competition/validate_submission.py <submission_dir>

Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import importlib.util
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_NAUTILUS_SRC = _REPO_ROOT / "nautilus" / "src"
_COMPETITION_DIR = Path(__file__).resolve().parent
if str(_NAUTILUS_SRC) not in sys.path:
    sys.path.insert(0, str(_NAUTILUS_SRC))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_COMPETITION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPETITION_DIR))

from timi_laws import LawViolation, enforce_laws  # noqa: E402

REQUIRED_MANIFEST_KEYS: frozenset[str] = frozenset(
    {
        "strategy_class_name",
        "config_class_name",
        "instrument_id",
        "bar_type",
        "default_config",
        "description",
    }
)

PROHIBITED_IDENTIFIERS: tuple[str, ...] = (
    "leverage",
    "margin",
    "futures",
    "short_sell",
    "isolated_margin",
    "cross_margin",
)

FLOAT_MONEY_KEYWORDS: tuple[str, ...] = (
    "cash",
    "notional",
    "equity",
    "pnl",
    "balance",
)


@dataclass
class CheckResult:
    """Result of an individual validation check."""

    step: int
    title: str
    status: str  # "OK" | "FAIL" | "WARN"
    detail: str = ""


@dataclass
class Report:
    """Aggregated report for one submission."""

    submission_dir: Path
    results: list[CheckResult] = field(default_factory=list)

    def add(self, step: int, title: str, status: str, detail: str = "") -> None:
        self.results.append(CheckResult(step=step, title=title, status=status, detail=detail))

    def has_failures(self) -> bool:
        return any(r.status == "FAIL" for r in self.results)

    def failure_count(self) -> int:
        return sum(1 for r in self.results if r.status == "FAIL")

    def render(self) -> str:
        lines: list[str] = []
        lines.append(f"Validating submission: {self.submission_dir}")
        lines.append("-" * 40)
        for r in self.results:
            marker = f"[{r.status}]".ljust(7)
            lines.append(f"{marker}{r.step}. {r.title}")
            if r.detail:
                for dline in r.detail.splitlines():
                    lines.append(f"       {dline}")
        lines.append("-" * 40)
        if self.has_failures():
            lines.append(f"FAIL: {self.failure_count()} checks failed.")
        else:
            lines.append("PASS: Submission is pluggable.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 1: Directory structure
# ---------------------------------------------------------------------------


def check_directory_structure(submission_dir: Path, report: Report) -> bool:
    """Verify strategy.py, tests/test_strategy.py, and research/ all exist."""
    missing: list[str] = []

    strategy_path = submission_dir / "strategy.py"
    if not strategy_path.is_file():
        missing.append("strategy.py")

    test_path = submission_dir / "tests" / "test_strategy.py"
    if not test_path.is_file():
        missing.append("tests/test_strategy.py")

    research_path = submission_dir / "research"
    if not research_path.is_dir():
        missing.append("research/ (directory)")

    if missing:
        detail = "Missing required paths:\n" + "\n".join(f"  - {m}" for m in missing)
        report.add(1, "Directory structure", "FAIL", detail)
        return False

    report.add(1, "Directory structure", "OK")
    return True


# ---------------------------------------------------------------------------
# Step 2: Load module and validate MANIFEST
# ---------------------------------------------------------------------------


def load_strategy_module(strategy_path: Path) -> ModuleType:
    """Load ``strategy.py`` as a uniquely named module.

    Unique naming prevents importlib from caching collisions between multiple
    validator invocations in the same Python process (e.g. the pytest suite).
    """
    unique_name = f"competition_submission_{id(strategy_path)}"
    spec = importlib.util.spec_from_file_location(unique_name, strategy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to build spec for {strategy_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as err:
        sys.modules.pop(unique_name, None)
        raise ImportError(f"Failed to import {strategy_path}: {err}") from err
    return module


def check_manifest(module: ModuleType, report: Report) -> dict[str, Any] | None:
    """Verify MANIFEST exists and has the exact required keys."""
    manifest = getattr(module, "MANIFEST", None)
    if manifest is None:
        report.add(
            2,
            "Manifest has 6 required keys",
            "FAIL",
            "Module-level MANIFEST attribute is missing from strategy.py.",
        )
        return None

    if not isinstance(manifest, dict):
        report.add(
            2,
            "Manifest has 6 required keys",
            "FAIL",
            f"MANIFEST must be a dict, got {type(manifest).__name__}",
        )
        return None

    keys = set(manifest.keys())
    missing = REQUIRED_MANIFEST_KEYS - keys
    extra = keys - REQUIRED_MANIFEST_KEYS

    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"Missing keys: {sorted(missing)}")
        if extra:
            parts.append(f"Extra keys: {sorted(extra)}")
        report.add(2, "Manifest has 6 required keys", "FAIL", "\n".join(parts))
        return None

    report.add(2, "Manifest has 6 required keys", "OK")
    return manifest


# ---------------------------------------------------------------------------
# Step 3: Class identity checks
# ---------------------------------------------------------------------------


def check_strategy_and_config_classes(
    module: ModuleType,
    manifest: dict[str, Any],
    report: Report,
) -> tuple[type | None, type | None]:
    """Verify strategy class subclasses Strategy and config extends StrategyConfig."""
    from nautilus_trader.config import StrategyConfig
    from nautilus_trader.trading.strategy import Strategy

    strategy_name = manifest["strategy_class_name"]
    config_name = manifest["config_class_name"]

    strategy_cls = getattr(module, strategy_name, None)
    config_cls = getattr(module, config_name, None)

    if strategy_cls is None:
        report.add(
            3,
            "Strategy class is a Strategy subclass",
            "FAIL",
            f"Class {strategy_name!r} not found in module.",
        )
    elif not isinstance(strategy_cls, type) or not issubclass(strategy_cls, Strategy):
        report.add(
            3,
            "Strategy class is a Strategy subclass",
            "FAIL",
            f"{strategy_name} is not a subclass of nautilus_trader.trading.strategy.Strategy",
        )
        strategy_cls = None
    else:
        report.add(3, "Strategy class is a Strategy subclass", "OK")

    if config_cls is None:
        report.add(
            3,
            "Config class inherits StrategyConfig",
            "FAIL",
            f"Class {config_name!r} not found in module.",
        )
    elif not isinstance(config_cls, type) or not issubclass(config_cls, StrategyConfig):
        report.add(
            3,
            "Config class inherits StrategyConfig",
            "FAIL",
            f"{config_name} does not inherit from nautilus_trader.config.StrategyConfig",
        )
        config_cls = None
    else:
        report.add(3, "Config class inherits StrategyConfig", "OK")

    return strategy_cls, config_cls


# ---------------------------------------------------------------------------
# Step 4: Frozen config check
# ---------------------------------------------------------------------------


def check_config_frozen(
    config_cls: type,
    manifest: dict[str, Any],
    report: Report,
) -> Any | None:
    """Instantiate the config and verify mutation raises."""
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.identifiers import InstrumentId

    try:
        instrument_id = InstrumentId.from_str(manifest["instrument_id"])
        bar_type = BarType.from_str(manifest["bar_type"])
    except Exception as err:
        report.add(
            4,
            "Config class is frozen",
            "FAIL",
            f"Could not parse instrument_id/bar_type: {err}",
        )
        return None

    default_cfg = dict(manifest.get("default_config") or {})
    # Per contract, default_config must NOT include instrument_id/bar_type.
    default_cfg.pop("instrument_id", None)
    default_cfg.pop("bar_type", None)

    try:
        instance = config_cls(
            instrument_id=instrument_id,
            bar_type=bar_type,
            **default_cfg,
        )
    except Exception as err:
        report.add(
            4,
            "Config class is frozen",
            "FAIL",
            f"Could not instantiate {config_cls.__name__}: {err}",
        )
        return None

    # Struct-level frozen flag
    struct_config = getattr(config_cls, "__struct_config__", None)
    if struct_config is None or not getattr(struct_config, "frozen", False):
        report.add(
            4,
            "Config class is frozen",
            "FAIL",
            f"{config_cls.__name__} is not a msgspec Struct with frozen=True",
        )
        return instance

    # Runtime mutation check
    try:
        instance.instrument_id = instrument_id  # type: ignore[misc]
    except (AttributeError, TypeError):
        report.add(4, "Config class is frozen", "OK")
        return instance

    report.add(
        4,
        "Config class is frozen",
        "FAIL",
        (
            f"Config class {config_cls.__name__} allowed mutation: "
            "c.instrument_id = ... did not raise"
        ),
    )
    return instance


# ---------------------------------------------------------------------------
# Step 5: Hard static checks (AST + token scan)
# ---------------------------------------------------------------------------


def _strip_comments_and_docstrings(source: str, tree: ast.AST) -> str:
    """Return the source with comments and docstring literals blanked out.

    Used for keyword scans that must ignore comments and docstrings. Docstring
    lines are replaced with whitespace (preserving line numbers so error
    messages stay accurate), then any ``#``-prefixed tail is dropped.
    """
    blanked_lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        if ast.get_docstring(node, clean=False) is None:
            continue
        first = node.body[0]
        if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
            continue
        start_lineno = first.lineno - 1
        end_lineno = (first.end_lineno or first.lineno) - 1
        for ln in range(start_lineno, end_lineno + 1):
            if 0 <= ln < len(blanked_lines):
                blanked_lines[ln] = " " * len(blanked_lines[ln])

    cleaned: list[str] = []
    for line in blanked_lines:
        hash_idx = line.find("#")
        cleaned.append(line[:hash_idx] if hash_idx >= 0 else line)
    return "\n".join(cleaned)


def _find_print_calls(tree: ast.AST) -> list[int]:
    """Return line numbers of bare ``print(...)`` calls."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]


def _find_prohibited_identifiers(cleaned_source: str) -> list[tuple[str, int]]:
    """Find prohibited keywords (leverage, margin, etc.) in source text.

    Operates on source with comments and docstrings stripped. Uses
    case-insensitive word-boundary matching via simple lowercase scan.
    """
    hits: list[tuple[str, int]] = []
    lower_lines = [line.lower() for line in cleaned_source.splitlines()]
    for lineno, line in enumerate(lower_lines, start=1):
        for keyword in PROHIBITED_IDENTIFIERS:
            idx = 0
            while True:
                pos = line.find(keyword, idx)
                if pos == -1:
                    break
                # Word-boundary check
                before_ok = pos == 0 or not (line[pos - 1].isalnum() or line[pos - 1] == "_")
                end = pos + len(keyword)
                after_ok = end >= len(line) or not (line[end].isalnum() or line[end] == "_")
                if before_ok and after_ok:
                    hits.append((keyword, lineno))
                idx = pos + len(keyword)
    return hits


def _arg_name(arg: ast.expr) -> str | None:
    """Return the identifier (or attribute tail) of a call argument."""
    if isinstance(arg, ast.Name):
        return arg.id
    if isinstance(arg, ast.Attribute):
        return arg.attr
    return None


def _find_round_on_price(tree: ast.AST) -> list[int]:
    """Return line numbers of ``round()`` calls where any arg name contains 'price'."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "round":
            continue
        for arg in node.args:
            name = _arg_name(arg)
            if name and "price" in name.lower():
                lines.append(node.lineno)
                break
    return lines


def _uses_order_apis(tree: ast.AST) -> bool:
    """Return True if strategy invokes submit_order or order_factory in code.

    AST-based (not substring): walks for ``Attribute`` nodes named
    ``submit_order`` or ``order_factory``. This avoids false positives from
    docstrings and comments that mention the API name without calling it.
    """
    order_apis = {"submit_order", "order_factory"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in order_apis:
            return True
        if isinstance(node, ast.Name) and node.id in order_apis:
            return True
    return False


def _uses_make_price_or_qty(tree: ast.AST) -> bool:
    """Return True if source has any make_price( or make_qty( call."""
    targets = {"make_price", "make_qty"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in targets:
            return True
        if isinstance(func, ast.Name) and func.id in targets:
            return True
    return False


def _find_float_monetary(tree: ast.AST) -> list[tuple[str, int]]:
    """Soft warning: float() calls where any arg name contains money keywords."""
    warnings: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "float":
            continue
        for arg in node.args:
            name = _arg_name(arg)
            if not name:
                continue
            lowered = name.lower()
            if any(kw in lowered for kw in FLOAT_MONEY_KEYWORDS):
                warnings.append((name, node.lineno))
                break
    return warnings


def check_static_constraints(strategy_path: Path, report: Report) -> None:
    """Run AST/regex-level safety checks on the strategy source file."""
    source = strategy_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(strategy_path))
    except SyntaxError as err:
        report.add(
            5,
            "No print() calls",
            "FAIL",
            f"strategy.py has a syntax error: {err}",
        )
        return

    # 5a. print() calls
    print_lines = _find_print_calls(tree)
    if print_lines:
        line_str = ", ".join(str(line) for line in print_lines)
        report.add(
            5,
            "No print() calls",
            "FAIL",
            f"Found print() at line(s): {line_str}. Use self.log.info/warning/error.",
        )
    else:
        report.add(5, "No print() calls", "OK")

    # 5b. Prohibited identifiers
    cleaned = _strip_comments_and_docstrings(source, tree)
    prohibited_hits = _find_prohibited_identifiers(cleaned)
    if prohibited_hits:
        detail = "Found prohibited identifiers (spot-only submissions):\n" + "\n".join(
            f"  - {kw} at line {ln}" for kw, ln in prohibited_hits
        )
        report.add(
            5,
            "No prohibited identifiers (leverage/margin/futures/short_sell)",
            "FAIL",
            detail,
        )
    else:
        report.add(
            5,
            "No prohibited identifiers (leverage/margin/futures/short_sell)",
            "OK",
        )

    # 5c. round() on price
    round_lines = _find_round_on_price(tree)
    if round_lines:
        line_str = ", ".join(str(line) for line in round_lines)
        report.add(
            5,
            "No round() on price variables",
            "FAIL",
            (
                f"Found round() applied to price-like variable at line(s): {line_str}. "
                "Use instrument.make_price(raw) instead."
            ),
        )
    else:
        report.add(5, "No round() on price variables", "OK")

    # 5d. make_price / make_qty presence if order APIs used
    if _uses_order_apis(tree):
        if _uses_make_price_or_qty(tree):
            report.add(5, "make_qty/make_price usage present", "OK")
        else:
            report.add(
                5,
                "make_qty/make_price usage present",
                "FAIL",
                (
                    "Strategy references submit_order/order_factory but has no "
                    "make_price()/make_qty() calls. Raw-rounded orders will be "
                    "rejected by Binance PRICE_FILTER / LOT_SIZE."
                ),
            )
    else:
        report.add(
            5,
            "make_qty/make_price usage present",
            "OK",
            "Skipped: strategy places no orders.",
        )

    # 5e. Soft warnings: float() on monetary variables
    float_hits = _find_float_monetary(tree)
    if float_hits:
        detail = "float() used on monetary-looking variables:\n" + "\n".join(
            f"  - float({name}) at line {ln}" for name, ln in float_hits
        )
        report.add(
            5,
            "Monetary values use Decimal not float",
            "WARN",
            detail,
        )


# ---------------------------------------------------------------------------
# Step 6: Run the submission's pytest suite
# ---------------------------------------------------------------------------


def check_tests_pass(submission_dir: Path, report: Report) -> None:
    """Run ``pytest`` on the submission's test file and capture the summary."""
    test_path = submission_dir / "tests" / "test_strategy.py"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q", "--no-header"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        report.add(
            6,
            "Tests pass",
            "FAIL",
            "pytest timed out after 120s running the submission tests.",
        )
        return
    except FileNotFoundError as err:
        report.add(6, "Tests pass", "FAIL", f"Could not invoke pytest: {err}")
        return

    stdout_tail = "\n".join(result.stdout.strip().splitlines()[-5:])
    if result.returncode == 0:
        summary = stdout_tail.splitlines()[-1] if stdout_tail else "passed"
        report.add(6, "Tests pass", "OK", summary)
    else:
        detail = f"pytest exited with code {result.returncode}\n{stdout_tail}"
        report.add(6, "Tests pass", "FAIL", detail)


# ---------------------------------------------------------------------------
# Step 7: Live runner config build (dry run, no network)
# ---------------------------------------------------------------------------


def check_live_runner_build(
    strategy_cls: type,
    config_cls: type,
    manifest: dict[str, Any],
    report: Report,
) -> None:
    """Synthetic dry-run: build a TradingNodeConfig for the submitted strategy.

    We don't rely on the submission being importable via a dotted module path
    (``competition/agent-5-hybrid/round11`` has dashes and is not a valid
    Python package). Instead we use the strategy's own ``__module__`` and
    qualified class name to build an ``ImportableStrategyConfig``-equivalent
    path. If the submission module was loaded via ``importlib.util`` and its
    dotted path doesn't exist on ``sys.path``, we fall back to verifying the
    classes can be wired into a live-style config manually using the live
    runner's helper.
    """
    try:
        from nautilus_trading.live.runner import build_live_config
    except ImportError as err:
        report.add(
            7,
            "Live runner can build config",
            "FAIL",
            f"Could not import build_live_config: {err}",
        )
        return

    default_cfg = dict(manifest.get("default_config") or {})
    default_cfg.pop("instrument_id", None)
    default_cfg.pop("bar_type", None)
    strategy_config_dict: dict[str, Any] = {
        **default_cfg,
        "instrument_id": manifest["instrument_id"],
        "bar_type": manifest["bar_type"],
    }

    # Use the already-imported classes' modules. These may or may not be on
    # sys.path — that's fine, we're only asking the config factory to build
    # an ImportableStrategyConfig, which doesn't resolve the import path
    # until the node actually runs.
    strategy_import_path = f"{strategy_cls.__module__}:{strategy_cls.__name__}"
    config_import_path = f"{config_cls.__module__}:{config_cls.__name__}"

    try:
        build_live_config(
            strategy_path=strategy_import_path,
            config_path=config_import_path,
            strategy_config=strategy_config_dict,
            instrument_id=manifest["instrument_id"],
            account_type="SPOT",
            testnet=True,
        )
    except (ImportError, TypeError, ValueError) as err:
        report.add(
            7,
            "Live runner can build config",
            "FAIL",
            f"build_live_config raised {type(err).__name__}: {err}",
        )
        return
    except Exception as err:
        # msgspec.ValidationError, pydantic, etc. — keep the blast radius.
        report.add(
            7,
            "Live runner can build config",
            "FAIL",
            f"build_live_config raised {type(err).__name__}: {err}",
        )
        return

    report.add(7, "Live runner can build config", "OK")


# ---------------------------------------------------------------------------
# Step 8: TiMi programming laws (opt-in)
# ---------------------------------------------------------------------------


def _should_enforce_laws(submission_dir: Path, flag: bool) -> bool:
    """Return True if the three TiMi laws should run on this submission.

    Two activation modes:
    1. Explicit: the caller passed ``--enforce-timi-laws``.
    2. Auto-detect: any segment of the resolved path matches ``agent-*-timi``.
    """
    if flag:
        return True
    return any(fnmatch.fnmatch(part, "agent-*-timi") for part in submission_dir.parts)


def _format_violations(violations: list[LawViolation]) -> str:
    """Render a grouped violation summary for the check report."""
    by_law: dict[str, list[LawViolation]] = {}
    for violation in violations:
        by_law.setdefault(violation.law, []).append(violation)
    lines: list[str] = []
    for law in ("cohesion", "dependency", "externalization"):
        for violation in by_law.get(law, []):
            lines.append(
                f"  - [{violation.law}] {violation.file.name}:"
                f"{violation.line}:{violation.col}: {violation.message}"
            )
    return "\n".join(lines)


def check_timi_laws(submission_dir: Path, report: Report) -> None:
    """Run the three TiMi programming laws and record any violations."""
    violations = enforce_laws(submission_dir)
    if violations:
        detail = "Found TiMi law violation(s):\n" + _format_violations(violations)
        report.add(8, "TiMi programming laws", "FAIL", detail)
    else:
        report.add(8, "TiMi programming laws", "OK")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def validate(submission_dir: Path, enforce_timi_laws: bool = False) -> Report:
    """Run all checks on a submission directory and return the full report.

    Set ``enforce_timi_laws=True`` (or put the submission under
    ``agent-*-timi/``) to also run the three TiMi programming laws in a
    step-8 gate.
    """
    report = Report(submission_dir=submission_dir)

    if not check_directory_structure(submission_dir, report):
        return report

    strategy_path = submission_dir / "strategy.py"
    try:
        module = load_strategy_module(strategy_path)
    except Exception as err:
        tb_tail = "\n".join(traceback.format_exception_only(type(err), err))
        report.add(
            2,
            "Manifest has 6 required keys",
            "FAIL",
            f"Could not import strategy.py: {tb_tail.strip()}",
        )
        return report

    manifest = check_manifest(module, report)
    if manifest is None:
        return report

    strategy_cls, config_cls = check_strategy_and_config_classes(module, manifest, report)

    if config_cls is not None:
        check_config_frozen(config_cls, manifest, report)

    # Static checks always run (don't depend on successful import beyond file existing)
    check_static_constraints(strategy_path, report)

    check_tests_pass(submission_dir, report)

    if strategy_cls is not None and config_cls is not None:
        check_live_runner_build(strategy_cls, config_cls, manifest, report)

    if _should_enforce_laws(submission_dir, enforce_timi_laws):
        check_timi_laws(submission_dir, report)

    return report


def main(argv: list[str]) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="validate_submission.py",
        description="R11+ competition submission validator.",
    )
    parser.add_argument("submission_dir", type=Path, help="Submission directory")
    parser.add_argument(
        "--enforce-timi-laws",
        action="store_true",
        help=(
            "Also enforce TiMi's three programming laws (auto-enabled for "
            "submissions under agent-*-timi/)."
        ),
    )
    ns = parser.parse_args(argv[1:])

    submission_dir = ns.submission_dir.resolve()
    if not submission_dir.is_dir():
        print(f"Not a directory: {submission_dir}")
        return 2

    report = validate(submission_dir, enforce_timi_laws=ns.enforce_timi_laws)
    print(report.render())
    return 1 if report.has_failures() else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
