"""Tests for ``competition/timi_orchestrator.py``.

All tests route every agent invocation through ``StubInvoker`` so no
real ``claude -p`` subprocess fires in CI. The stubs write fixture
directive files into a temporary reflection directory (monkey-patched
via the module-level ``REFLECTION_DIR``) and simulate A_be writing a
full submission by copying the repo TEMPLATE.

Covered scenarios (from the task brief):

1. **Happy path**: 2 pairs, both converge in a couple of iterations.
2. **Budget exhausted**: one pair chews through the $5 budget before
   emitting a verdict.
3. **Diverged**: one pair's A_fr emits ``verdict: diverged`` at iter 2.

Plus a pair of parser-level tests for ``parse_directive`` covering:

* happy YAML-frontmatter round trip
* missing frontmatter delimiter
* malformed envelope field (bad ``author``)
* parameter directive with a broken JSON patch
"""

from __future__ import annotations

import shutil
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_COMPETITION_DIR = _REPO_ROOT / "competition"
if str(_COMPETITION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPETITION_DIR))

from competition import timi_orchestrator as orch  # noqa: E402
from competition.timi_orchestrator import (  # noqa: E402
    OUTCOME_BUDGET_EXHAUSTED,
    OUTCOME_CONVERGED,
    OUTCOME_DIVERGED,
    Directive,
    DirectiveSchemaViolation,
    HiddenWindowGuard,
    StubInvoker,
    StubResponse,
    parse_directive,
    run_round,
)

_TEMPLATE_DIR = _REPO_ROOT / "competition" / "TEMPLATE"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Redirect every orchestrator-managed directory into ``tmp_path``.

    The orchestrator writes under module-level constants
    (``REFLECTION_DIR``, ``TIMI_AGENT_ROOT``, etc.). Patching them here
    keeps the test hermetic — no files are created in the real repo
    tree and no pre-existing ``docs/research/timi/reflection`` state
    pollutes the assertions.
    """
    reflection_dir = tmp_path / "reflection"
    timi_root = tmp_path / "competition" / "agent-6-timi"
    reflection_dir.mkdir(parents=True)
    timi_root.mkdir(parents=True)

    monkeypatch.setattr(orch, "REFLECTION_DIR", reflection_dir)
    monkeypatch.setattr(orch, "TIMI_AGENT_ROOT", timi_root)
    # Keep the agent-*-timi path segment so validate_submission auto-
    # enables the 3-laws check (via its fnmatch on path parts).
    return {"reflection": reflection_dir, "timi_root": timi_root, "tmp": tmp_path}


@pytest.fixture
def patched_round_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    """Rebuild ``_load_round_config`` to return a 2-pair config.

    Hard-wired to BTCUSDT + ETHUSDT and a trivial eval window so the
    HiddenWindowGuard assertions still run.
    """
    fake_config = {
        "round": 11,
        "eval_period": {"start": "2099-01-01", "end": "2099-12-31"},
        "initial_capital_usdt": Decimal("1000.00"),
        "instruments_allowlist": [
            "BTCUSDT.BINANCE",
            "ETHUSDT.BINANCE",
        ],
    }

    def fake_load(round_number: int) -> dict[str, object]:
        return dict(fake_config)

    monkeypatch.setattr(orch, "_load_round_config", fake_load)
    return fake_config


def _write_valid_directive(
    reflection_dir: Path,
    *,
    round_number: int,
    pair: str,
    iteration: int,
    verdict: str,
    layer: str = "parameter",
) -> Path:
    """Write a minimal valid A_fr directive markdown file.

    Always includes a one-entry JSON patch block so
    ``parse_directive`` can decode it even for converged/diverged
    verdicts (the orchestrator only routes on verdict but the parser
    still walks the body).
    """
    iter_str = f"{iteration:02d}"
    path = reflection_dir / f"round{round_number}__{pair}__{iter_str}.md"
    path.write_text(
        f"""---
round: {round_number}
pair: "{pair}"
iter: {iteration}
layer: {layer}
verdict: {verdict}
author: A_fr
timestamp: "2026-04-09T14:00:00Z"
human_review_required: false
---

## Risk scenario
Stub scenario for tests.

```json
[
  {{
    "field": "fast_period",
    "old_value": 12,
    "new_value": 9,
    "justification": "stub",
    "lp_solution_ref": "#lp-1"
  }}
]
```
""",
        encoding="utf-8",
    )
    return path


def _populate_pair_submission(pair_dir: Path) -> None:
    """Simulate A_be by copying TEMPLATE into the pair directory.

    Used as a side effect on the A_be stub responses so the orchestrator's
    validator + per-pair backtest have something real to chew on.
    """
    if pair_dir.exists():
        for entry in pair_dir.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    shutil.copytree(_TEMPLATE_DIR, pair_dir, dirs_exist_ok=True)
    # Scrub any stale pycache so `ast.parse` sees clean sources.
    for cache in pair_dir.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


def test_parse_directive_happy_path(tmp_path: Path) -> None:
    """A minimal valid parameter directive parses cleanly."""
    path = tmp_path / "round11__BTCUSDT__02.md"
    path.write_text(
        """---
round: 11
pair: "BTCUSDT"
iter: 2
layer: parameter
verdict: continue
author: A_fr
timestamp: "2026-04-09T14:22:31Z"
human_review_required: false
---

Body prose.

```json
[
  {
    "field": "stop_loss_pct",
    "old_value": 0.025,
    "new_value": 0.018,
    "justification": "Tighten stop",
    "lp_solution_ref": "#lp-1"
  }
]
```
""",
        encoding="utf-8",
    )

    result = parse_directive(path)

    assert isinstance(result, Directive)
    assert result.envelope.round == 11
    assert result.envelope.pair == "BTCUSDT"
    assert result.envelope.iter == 2
    assert result.envelope.layer == "parameter"
    assert result.envelope.verdict == "continue"
    assert result.envelope.author == "A_fr"
    assert result.envelope.human_review_required is False
    assert len(result.patches) == 1
    assert result.patches[0]["field"] == "stop_loss_pct"


def test_parse_directive_missing_frontmatter(tmp_path: Path) -> None:
    """A file with no ``---`` frontmatter must raise a schema violation."""
    path = tmp_path / "broken.md"
    path.write_text("no frontmatter here, just prose\n", encoding="utf-8")

    with pytest.raises(DirectiveSchemaViolation, match="frontmatter"):
        parse_directive(path)


def test_parse_directive_rejects_non_a_fr_author(tmp_path: Path) -> None:
    """Envelope.author must be literally ``A_fr``."""
    path = tmp_path / "round11__BTCUSDT__01.md"
    path.write_text(
        """---
round: 11
pair: "BTCUSDT"
iter: 1
layer: parameter
verdict: continue
author: A_be
timestamp: "2026-04-09T14:22:31Z"
human_review_required: false
---

body
""",
        encoding="utf-8",
    )

    with pytest.raises(DirectiveSchemaViolation, match="author"):
        parse_directive(path)


def test_parse_directive_parameter_requires_patch_block(tmp_path: Path) -> None:
    """Parameter directives must include a ```json patch block."""
    path = tmp_path / "no_patch.md"
    path.write_text(
        """---
round: 11
pair: "BTCUSDT"
iter: 1
layer: parameter
verdict: continue
author: A_fr
timestamp: "2026-04-09T14:22:31Z"
human_review_required: false
---

prose only, no json block
""",
        encoding="utf-8",
    )

    with pytest.raises(DirectiveSchemaViolation, match="json"):
        parse_directive(path)


# ---------------------------------------------------------------------------
# HiddenWindowGuard
# ---------------------------------------------------------------------------


def test_hidden_window_guard_redacts_config() -> None:
    """The eval window must be scrubbed out of the config passed to agents."""
    guard = HiddenWindowGuard(eval_start="2025-10-01", eval_end="2025-12-31")
    raw = {
        "round": 11,
        "eval_period": {"start": "2025-10-01", "end": "2025-12-31"},
        "initial_capital_usdt": Decimal("1000"),
    }
    redacted = guard.redact_round_config(raw)
    assert redacted["round"] == 11
    assert redacted["initial_capital_usdt"] == Decimal("1000")
    assert redacted["eval_period"] == "[REDACTED — orchestrator stripped]"


def test_hidden_window_guard_refuses_leaked_prompt() -> None:
    """A prompt body that mentions either date must raise."""
    guard = HiddenWindowGuard(eval_start="2025-10-01", eval_end="2025-12-31")
    guard.assert_prompt_clean("nothing sensitive here")
    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="eval window"):
        guard.assert_prompt_clean("oops date leak 2025-12-31 inside")


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_run_round_dry_run(
    isolated_dirs: dict[str, Path],
    patched_round_config: dict[str, object],
) -> None:
    """``dry_run=True`` returns a report without invoking any agent."""
    report = run_round(11, dry_run=True)
    assert report.round_number == 11
    assert report.outcome == "dry_run"
    assert {p.pair for p in report.per_pair_reports} == {"BTCUSDT", "ETHUSDT"}
    assert report.total_token_cost_usd == Decimal("0")
    # No ROUND_REPORT.md written in dry-run mode.
    round_dir = isolated_dirs["timi_root"] / "round11"
    assert not (round_dir / "ROUND_REPORT.md").exists()


# ---------------------------------------------------------------------------
# Happy path: both pairs converge in 2 iterations
# ---------------------------------------------------------------------------


def test_run_round_happy_path_two_pairs_converge(
    isolated_dirs: dict[str, Path],
    patched_round_config: dict[str, object],
) -> None:
    """Both pairs reach ``verdict: converged`` inside their budget + iter cap."""
    reflection_dir: Path = isolated_dirs["reflection"]
    timi_root: Path = isolated_dirs["timi_root"]

    def write_directive_for(pair: str, iteration: int, verdict: str) -> callable:
        def _effect() -> None:
            _write_valid_directive(
                reflection_dir,
                round_number=11,
                pair=pair,
                iteration=iteration,
                verdict=verdict,
            )

        return _effect

    def populate_pair(pair: str) -> callable:
        def _effect() -> None:
            _populate_pair_submission(timi_root / "round11" / pair)

        return _effect

    responses: dict[str, list[StubResponse]] = {
        "a_ma": [StubResponse(stdout="MACRO_ANALYSIS_COMPLETE\n")],
        "a_sa": [
            StubResponse(stdout="STRATEGY_ADAPTATION_COMPLETE\n"),
            StubResponse(stdout="STRATEGY_ADAPTATION_COMPLETE\n"),
        ],
        "a_be": [
            StubResponse(
                stdout="BOT_WRITTEN_AND_VALIDATED\n",
                side_effects=[populate_pair("BTCUSDT")],
            ),
            StubResponse(
                stdout="BOT_WRITTEN_AND_VALIDATED\n",
                side_effects=[populate_pair("BTCUSDT")],
            ),
            StubResponse(
                stdout="BOT_WRITTEN_AND_VALIDATED\n",
                side_effects=[populate_pair("ETHUSDT")],
            ),
            StubResponse(
                stdout="BOT_WRITTEN_AND_VALIDATED\n",
                side_effects=[populate_pair("ETHUSDT")],
            ),
        ],
        "a_fr": [
            StubResponse(
                stdout="REFLECTION_EMITTED\n",
                side_effects=[write_directive_for("BTCUSDT", 1, "continue")],
            ),
            StubResponse(
                stdout="REFLECTION_EMITTED\n",
                side_effects=[write_directive_for("BTCUSDT", 2, "converged")],
            ),
            StubResponse(
                stdout="REFLECTION_EMITTED\n",
                side_effects=[write_directive_for("ETHUSDT", 1, "continue")],
            ),
            StubResponse(
                stdout="REFLECTION_EMITTED\n",
                side_effects=[write_directive_for("ETHUSDT", 2, "converged")],
            ),
        ],
    }
    invoker = StubInvoker(responses)

    report = run_round(11, invoker=invoker)

    assert report.round_number == 11
    assert report.outcome == OUTCOME_CONVERGED
    # 1 A_ma + 2 A_sa + 4 A_be + 4 A_fr = 11 invocations
    assert len(invoker.invocations) == 11
    assert len(report.per_pair_reports) == 2
    for pr in report.per_pair_reports:
        assert pr.outcome == OUTCOME_CONVERGED
        assert pr.iterations == 2
    # Round report should have been written to the patched timi_root.
    round_report_path = timi_root / "round11" / "ROUND_REPORT.md"
    assert round_report_path.exists()
    content = round_report_path.read_text()
    assert "converged" in content
    assert "BTCUSDT" in content
    assert "ETHUSDT" in content


# ---------------------------------------------------------------------------
# Budget exhausted
# ---------------------------------------------------------------------------


def test_run_round_budget_exhausted(
    isolated_dirs: dict[str, Path],
    patched_round_config: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single pair hits the $5 budget before A_fr ever converges."""
    reflection_dir: Path = isolated_dirs["reflection"]
    timi_root: Path = isolated_dirs["timi_root"]
    # Only evaluate one pair for this scenario.
    monkeypatch.setattr(
        orch,
        "_pairs_from_config",
        lambda _cfg: ["BTCUSDT"],
    )
    # Force a tiny budget so the stub's $0.01/call burn exhausts it on
    # the 4th invocation (a_sa + 2x a_be + 1x a_fr + ...).
    monkeypatch.setattr(orch, "TOKEN_BUDGET_USD_PER_PAIR", Decimal("0.03"))

    def write_directive(iteration: int) -> callable:
        def _effect() -> None:
            _write_valid_directive(
                reflection_dir,
                round_number=11,
                pair="BTCUSDT",
                iteration=iteration,
                verdict="continue",
            )

        return _effect

    def populate() -> None:
        _populate_pair_submission(timi_root / "round11" / "BTCUSDT")

    responses: dict[str, list[StubResponse]] = {
        "a_ma": [StubResponse(stdout="MACRO_ANALYSIS_COMPLETE\n")],
        "a_sa": [StubResponse(stdout="STRATEGY_ADAPTATION_COMPLETE\n")],
        "a_be": [
            StubResponse(
                stdout="BOT_WRITTEN_AND_VALIDATED\n",
                side_effects=[populate],
            ),
            StubResponse(
                stdout="BOT_WRITTEN_AND_VALIDATED\n",
                side_effects=[populate],
            ),
            StubResponse(
                stdout="BOT_WRITTEN_AND_VALIDATED\n",
                side_effects=[populate],
            ),
            StubResponse(
                stdout="BOT_WRITTEN_AND_VALIDATED\n",
                side_effects=[populate],
            ),
            StubResponse(
                stdout="BOT_WRITTEN_AND_VALIDATED\n",
                side_effects=[populate],
            ),
        ],
        "a_fr": [
            StubResponse(
                stdout="REFLECTION_EMITTED\n",
                side_effects=[write_directive(1)],
            ),
            StubResponse(
                stdout="REFLECTION_EMITTED\n",
                side_effects=[write_directive(2)],
            ),
            StubResponse(
                stdout="REFLECTION_EMITTED\n",
                side_effects=[write_directive(3)],
            ),
            StubResponse(
                stdout="REFLECTION_EMITTED\n",
                side_effects=[write_directive(4)],
            ),
            StubResponse(
                stdout="REFLECTION_EMITTED\n",
                side_effects=[write_directive(5)],
            ),
        ],
    }
    invoker = StubInvoker(responses)

    report = run_round(11, invoker=invoker)

    assert report.outcome == OUTCOME_BUDGET_EXHAUSTED
    assert len(report.per_pair_reports) == 1
    assert report.per_pair_reports[0].outcome == OUTCOME_BUDGET_EXHAUSTED


# ---------------------------------------------------------------------------
# Diverged
# ---------------------------------------------------------------------------


def test_run_round_diverged_at_iter_2(
    isolated_dirs: dict[str, Path],
    patched_round_config: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A_fr emits ``verdict: diverged`` on iter 2 → pair is abandoned."""
    reflection_dir: Path = isolated_dirs["reflection"]
    timi_root: Path = isolated_dirs["timi_root"]
    monkeypatch.setattr(orch, "_pairs_from_config", lambda _cfg: ["BTCUSDT"])

    def write_directive(iteration: int, verdict: str) -> callable:
        def _effect() -> None:
            _write_valid_directive(
                reflection_dir,
                round_number=11,
                pair="BTCUSDT",
                iteration=iteration,
                verdict=verdict,
            )

        return _effect

    def populate() -> None:
        _populate_pair_submission(timi_root / "round11" / "BTCUSDT")

    responses: dict[str, list[StubResponse]] = {
        "a_ma": [StubResponse(stdout="MACRO_ANALYSIS_COMPLETE\n")],
        "a_sa": [StubResponse(stdout="STRATEGY_ADAPTATION_COMPLETE\n")],
        "a_be": [
            StubResponse(
                stdout="BOT_WRITTEN_AND_VALIDATED\n",
                side_effects=[populate],
            ),
            StubResponse(
                stdout="BOT_WRITTEN_AND_VALIDATED\n",
                side_effects=[populate],
            ),
        ],
        "a_fr": [
            StubResponse(
                stdout="REFLECTION_EMITTED\n",
                side_effects=[write_directive(1, "continue")],
            ),
            StubResponse(
                stdout="REFLECTION_EMITTED\n",
                side_effects=[write_directive(2, "diverged")],
            ),
        ],
    }
    invoker = StubInvoker(responses)

    report = run_round(11, invoker=invoker)

    assert report.outcome == OUTCOME_DIVERGED
    assert len(report.per_pair_reports) == 1
    pr = report.per_pair_reports[0]
    assert pr.outcome == OUTCOME_DIVERGED
    assert pr.iterations == 2
    # Trace should contain 4 records: 2x a_be + 2x a_fr.
    assert len(pr.convergence_trace) == 4
    assert pr.convergence_trace[-1].verdict == "diverged"
