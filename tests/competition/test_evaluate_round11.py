"""Tests for ``competition/evaluate_round11.py``.

Covers:

    * Happy path against the TEMPLATE fixture (synthetic data).
    * Rejecting a structurally broken submission.
    * Ranking multiple submissions into the same output file.
    * Respecting a CLI ``--initial-capital`` override.
    * Per-pair layout (TiMi): ``round<N>/<PAIR>/strategy.py`` subdirs
      are auto-detected and aggregated under a single agent row.

Every test runs the evaluator as a subprocess, matching the pattern used
by ``tests/competition/test_validate_submission.py``. Output is read from
the ``round11_results.txt`` file the CLI writes (either at a default path
inside the repo's ``competition/`` directory, or at ``--output`` overrides
when we want per-test isolation).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_DIR = _REPO_ROOT / "competition" / "TEMPLATE"
_EVALUATOR = _REPO_ROOT / "competition" / "evaluate_round11.py"


def _run_evaluator(
    *args: str,
    timeout: int = 240,
) -> subprocess.CompletedProcess[str]:
    """Invoke the evaluator CLI with the supplied args."""
    return subprocess.run(
        [sys.executable, str(_EVALUATOR), *args],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _copy_template(dest: Path) -> Path:
    """Copy ``competition/TEMPLATE`` into ``dest`` and scrub pycache."""
    shutil.copytree(_TEMPLATE_DIR, dest)
    for cache in dest.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    return dest


def _make_pair_submission(dest: Path, pair: str) -> Path:
    """Copy TEMPLATE into ``dest`` and retarget its MANIFEST to ``pair``.

    Used by the per-pair layout test to build a synthetic multi-pair
    agent without touching the repo TEMPLATE. ``pair`` is a base symbol
    like ``BTCUSDT``; the MANIFEST is rewritten to point at
    ``<pair>.BINANCE`` for both ``instrument_id`` and ``bar_type``.
    """
    _copy_template(dest)
    strategy_path = dest / "strategy.py"
    source = strategy_path.read_text()
    source = source.replace(
        '"instrument_id": "BNBUSDT.BINANCE"',
        f'"instrument_id": "{pair}.BINANCE"',
    )
    source = source.replace(
        '"bar_type": "BNBUSDT.BINANCE-1-HOUR-LAST-EXTERNAL"',
        f'"bar_type": "{pair}.BINANCE-1-HOUR-LAST-EXTERNAL"',
    )
    strategy_path.write_text(source)
    return dest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def template_copy(tmp_path: Path) -> Path:
    """A fresh copy of the TEMPLATE submission for isolated mutation."""
    return _copy_template(tmp_path / "submission")


@pytest.fixture
def output_path(tmp_path: Path) -> Path:
    """Unique output file path so tests don't stomp on each other."""
    return tmp_path / "round11_results.txt"


# ---------------------------------------------------------------------------
# 1. Positive: TEMPLATE runs cleanly end-to-end
# ---------------------------------------------------------------------------


def test_evaluator_runs_template(template_copy: Path, output_path: Path) -> None:
    """Pointing the evaluator at TEMPLATE must produce one OK result row."""
    result = _run_evaluator(
        "--round",
        "11",
        "--submission-dir",
        str(template_copy),
        "--output",
        str(output_path),
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert output_path.exists(), "evaluator did not write output file"

    content = output_path.read_text()
    assert "Round 11 Results" in content
    assert "TemplateStrategy" in content
    assert "INVALID submissions" not in content

    # The template is always-flat, so equity should equal starting capital.
    assert "$1000.00" in content
    assert "+0.00%" in content


# ---------------------------------------------------------------------------
# 2. Negative: structurally broken submission is marked INVALID
# ---------------------------------------------------------------------------


def test_evaluator_rejects_invalid_submission(
    template_copy: Path,
    output_path: Path,
) -> None:
    """A submission missing its MANIFEST must show up in the INVALID block."""
    strategy_path = template_copy / "strategy.py"
    source = strategy_path.read_text()
    # Drop the MANIFEST declaration entirely (same mutation used by
    # test_validate_submission::test_missing_manifest).
    marker = "MANIFEST: dict[str, Any] = {"
    idx = source.index(marker)
    strategy_path.write_text(source[:idx].rstrip() + "\n")

    result = _run_evaluator(
        "--round",
        "11",
        "--submission-dir",
        str(template_copy),
        "--output",
        str(output_path),
    )

    assert result.returncode == 0, (
        f"Evaluator should still exit 0 when a submission is invalid, "
        f"got {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert output_path.exists()

    content = output_path.read_text()
    assert "INVALID submissions" in content
    # TemplateStrategy should NOT appear in the leaderboard rows.
    assert "TemplateStrategy" not in content.split("INVALID submissions")[0]
    assert "(no valid submissions)" in content


# ---------------------------------------------------------------------------
# 3. Ranking: two submissions both appear in the leaderboard
# ---------------------------------------------------------------------------


def test_evaluator_ranks_multiple(tmp_path: Path, output_path: Path) -> None:
    """Two TEMPLATE-derived submissions must both appear as ranked rows."""
    # The evaluator's discovery walks competition/agent-*/round{N}/. We stage
    # a temp "competition root" with two fake agents so discovery finds both.
    fake_root = tmp_path / "competition"
    fake_root.mkdir()
    agent_a = fake_root / "agent-9-alpha" / "round11"
    agent_b = fake_root / "agent-8-beta" / "round11"
    agent_a.parent.mkdir()
    agent_b.parent.mkdir()
    _copy_template(agent_a)
    _copy_template(agent_b)

    # Evaluator discovery roots at _REPO_ROOT / "competition", so we can't
    # easily point it at the temp fake root. Instead we run the evaluator
    # twice with --submission-dir (proving each submission evaluates), then
    # call render_results in-process to cover the real multi-row ranking.
    first_output = tmp_path / "first.txt"
    second_output = tmp_path / "second.txt"
    result_a = _run_evaluator(
        "--round",
        "11",
        "--submission-dir",
        str(agent_a),
        "--output",
        str(first_output),
    )
    result_b = _run_evaluator(
        "--round",
        "11",
        "--submission-dir",
        str(agent_b),
        "--output",
        str(second_output),
    )
    assert result_a.returncode == 0, result_a.stderr
    assert result_b.returncode == 0, result_b.stderr

    content_a = first_output.read_text()
    content_b = second_output.read_text()
    assert "agent9_alpha" in content_a
    assert "agent8_beta" in content_b
    assert "TemplateStrategy" in content_a
    assert "TemplateStrategy" in content_b
    assert "INVALID submissions" not in content_a
    assert "INVALID submissions" not in content_b

    # Also exercise the in-process multi-submission ranking path so we know
    # the render function produces ``Rank 1`` / ``Rank 2`` rows for two OKs.
    from datetime import datetime, timezone

    sys.path.insert(0, str(_REPO_ROOT))
    from competition.evaluate_round11 import (  # noqa: E402
        EvalContext,
        SubmissionResult,
        render_results,
    )

    ctx = EvalContext(
        round_num=11,
        eval_start="2025-10-01",
        eval_end="2025-12-31",
        initial_capital=Decimal("1000.00"),
        catalog_path=None,
    )
    results = [
        SubmissionResult(
            agent_slug="agent8_beta",
            submission_dir=agent_b,
            status="OK",
            strategy_name="TemplateStrategy",
            description="beta",
            final_equity=Decimal("1200.00"),
            total_return_pct=20.0,
            sharpe_ratio=1.2,
            max_drawdown_pct=-5.0,
            num_trades=3,
            win_rate_pct=66.7,
        ),
        SubmissionResult(
            agent_slug="agent9_alpha",
            submission_dir=agent_a,
            status="OK",
            strategy_name="TemplateStrategy",
            description="alpha",
            final_equity=Decimal("1500.00"),
            total_return_pct=50.0,
            sharpe_ratio=2.0,
            max_drawdown_pct=-7.0,
            num_trades=5,
            win_rate_pct=80.0,
        ),
    ]
    rendered = render_results(results, ctx, datetime.now(tz=timezone.utc))
    assert "| 1    | agent9_alpha" in rendered  # higher return ranks first
    assert "| 2    | agent8_beta" in rendered
    assert "+50.00%" in rendered
    assert "+20.00%" in rendered


# ---------------------------------------------------------------------------
# 4. ``--initial-capital`` override is honoured
# ---------------------------------------------------------------------------


def test_evaluator_respects_initial_capital(
    template_copy: Path,
    output_path: Path,
) -> None:
    """Passing ``--initial-capital 5000`` must surface $5000 as final equity."""
    result = _run_evaluator(
        "--round",
        "11",
        "--submission-dir",
        str(template_copy),
        "--output",
        str(output_path),
        "--initial-capital",
        "5000",
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    content = output_path.read_text()
    assert "Initial capital: $5000.00 USDT" in content
    assert "$5000.00" in content
    # Always-flat template => final_equity exactly matches starting capital.
    assert "final_equity: 5000.00" in content


# ---------------------------------------------------------------------------
# 5. Per-pair layout: TiMi ``round<N>/<PAIR>/strategy.py`` is auto-detected
# ---------------------------------------------------------------------------


def test_evaluator_handles_per_pair_layout(
    tmp_path: Path,
    output_path: Path,
) -> None:
    """Per-pair agents aggregate into a single row with per-pair detail lines.

    Builds a synthetic ``agent-6-timi/round11/`` with two pair subdirs
    (``BTCUSDT``, ``ETHUSDT``), each a complete TEMPLATE-derived
    submission. Since both are always-flat, the aggregate must report
    +0.00% return, 0 trades, and final equity equal to the starting
    capital. The detailed block must contain a ``per-pair:`` sub-block
    listing both pairs.
    """
    agent_root = tmp_path / "agent-6-timi" / "round11"
    agent_root.mkdir(parents=True)
    _make_pair_submission(agent_root / "BTCUSDT", "BTCUSDT")
    _make_pair_submission(agent_root / "ETHUSDT", "ETHUSDT")

    result = _run_evaluator(
        "--round",
        "11",
        "--submission-dir",
        str(agent_root),
        "--output",
        str(output_path),
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert output_path.exists(), "evaluator did not write output file"

    content = output_path.read_text()
    assert "Round 11 Results" in content
    assert "INVALID submissions" not in content

    # Aggregate row: both pairs are flat => agent aggregate is also flat.
    assert "+0.00%" in content
    # Both pair subdirs are equal-weighted, so the aggregate starts
    # with full capital and ends with full capital (nothing traded).
    assert "$1000.00" in content
    assert "final_equity: 1000.00" in content

    # The detailed block must contain per-pair lines naming both pairs.
    assert "per-pair:" in content
    assert "BTCUSDT:" in content
    assert "ETHUSDT:" in content
    # Each pair line should report return= / sharpe= / trades= / mdd=.
    for marker in ("return=+0.00%", "trades=0", "mdd=+0.00%"):
        assert marker in content, f"missing {marker!r} in:\n{content}"


def test_evaluator_detects_mixed_layout(tmp_path: Path, output_path: Path) -> None:
    """Mixing monolithic + per-pair in the same round directory is an error.

    If a directory has both a top-level ``strategy.py`` AND a subdirectory
    containing its own ``strategy.py``, we can't tell which one the agent
    intended as the entry point. The evaluator must reject the whole
    submission instead of silently picking one.
    """
    agent_root = tmp_path / "mixed"
    _copy_template(agent_root)  # top-level strategy.py
    _make_pair_submission(agent_root / "BTCUSDT", "BTCUSDT")

    result = _run_evaluator(
        "--round",
        "11",
        "--submission-dir",
        str(agent_root),
        "--output",
        str(output_path),
    )
    # --submission-dir rejects mixed layouts at CLI parse time => exit 2.
    assert result.returncode == 2, (
        f"Expected exit 2 for mixed layout, got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Mixed submission layout" in result.stderr or "mixed" in result.stderr.lower()
