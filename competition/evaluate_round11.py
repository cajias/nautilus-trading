"""R11+ crypto competition evaluator.

Replaces the pre-R11 pandas-only evaluator (``evaluate_round10.py``). Each
submission now ships a NautilusTrader ``Strategy`` subclass plus a
``MANIFEST`` dict (see ``competition/COMPETITION.md``). This evaluator:

    1. Discovers submissions in ``competition/agent-*/round11/`` (or uses
       the ``--submission-dir`` override).
    2. Validates each submission against the R11+ contract using
       ``competition/validate_submission.py``.
    3. Instantiates the submitted ``Strategy`` + ``StrategyConfig`` from
       MANIFEST metadata and runs it through a ``BacktestEngine`` on the
       round 11 hidden eval window.
    4. Extracts final equity, return %, Sharpe, max drawdown, trade count,
       and win rate from the portfolio analyzer.
    5. Ranks submissions by return % and writes ``round11_results.txt``.

Usage (smoke test against the template, hermetic)::

    uv --project nautilus run python competition/evaluate_round11.py \\
        --round 11 --submission-dir competition/TEMPLATE \\
        --catalog-path tests/competition/fixtures/catalog --require-catalog

Data sources (no synthetic fallbacks):

    * ``--catalog-path`` + ``--require-catalog`` -- bars come exclusively
      from the supplied ``ParquetDataCatalog``. If the catalog is missing
      or returns 0 bars for the requested ``(instrument_id, bar_type)``
      the evaluator raises ``EvalDataError`` and exits non-zero. Use this
      for hermetic test runs.
    * ``--catalog-path`` only -- catalog is preferred; on miss the
      evaluator falls through to a real Binance public-klines fetch.
    * neither -- bars are fetched fresh from Binance for every submission.

If real data cannot be obtained, evaluation fails loudly. The evaluator
NEVER substitutes synthetic / generated data on the runtime path.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import math
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

# Ensure the nautilus_trading src tree and the repo root are importable before
# any NautilusTrader imports. Mirrors the pattern in validate_submission.py so
# the evaluator can be run directly (``python competition/evaluate_round11.py``).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_NAUTILUS_SRC = _REPO_ROOT / "nautilus" / "src"
if str(_NAUTILUS_SRC) not in sys.path:
    sys.path.insert(0, str(_NAUTILUS_SRC))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from competition.round11_config import ROUND_CONFIG  # noqa: E402
from competition.validate_submission import REQUIRED_MANIFEST_KEYS, validate  # noqa: E402
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig  # noqa: E402
from nautilus_trader.config import LoggingConfig  # noqa: E402
from nautilus_trader.model.currencies import USDT  # noqa: E402
from nautilus_trader.model.data import Bar, BarType  # noqa: E402
from nautilus_trader.model.enums import AccountType, OmsType  # noqa: E402
from nautilus_trader.model.identifiers import InstrumentId, Venue  # noqa: E402
from nautilus_trader.model.objects import Money, Price, Quantity  # noqa: E402
from nautilus_trader.persistence.catalog import ParquetDataCatalog  # noqa: E402

from nautilus_trading.data.providers import _build_crypto_instrument  # noqa: E402

logger = logging.getLogger(__name__)

BINANCE_VENUE = Venue("BINANCE")


class EvalDataError(RuntimeError):
    """Real market data was unavailable. Do not silently substitute fakes.

    Raised by ``load_catalog_bars`` and ``fetch_binance_bars`` when no
    real bars can be obtained for a requested ``(instrument_id, bar_type)``.
    The evaluator must propagate this as a fatal error rather than
    falling back to synthetic data -- a leaderboard from generated bars
    is worse than no leaderboard.
    """


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SubmissionResult:
    """Outcome of evaluating a single submission.

    ``per_pair_results`` is only populated when the underlying submission
    used the per-pair layout (``round<N>/<PAIR>/strategy.py`` subdirs). In
    that case the top-level fields hold the agent-wide aggregate metrics
    and each child ``SubmissionResult`` in ``per_pair_results`` carries
    the metrics for a single pair. For monolithic submissions the list
    stays empty.
    """

    agent_slug: str
    submission_dir: Path
    status: str  # "OK" | "INVALID" | "ERROR"
    strategy_name: str = ""
    description: str = ""
    final_equity: Decimal = Decimal("0")
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    num_trades: int = 0
    win_rate_pct: float = 0.0
    error: str = ""
    per_pair_results: list["SubmissionResult"] = field(default_factory=list)


@dataclass
class EvalContext:
    """Runtime parameters threaded through the evaluator."""

    round_num: int
    eval_start: str
    eval_end: str
    initial_capital: Decimal
    catalog_path: Path | None
    require_catalog: bool = False  # if True, never fall through to Binance fetch
    allowlist: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Submission discovery
# ---------------------------------------------------------------------------


def discover_submissions(competition_root: Path, round_num: int) -> list[Path]:
    """Return submission directories under ``competition/agent-*/round{N}/``.

    A directory is considered a submission if it contains ``strategy.py``
    at the top level (monolithic layout) or if any direct subdirectory
    contains its own ``strategy.py`` (per-pair layout, used by TiMi).
    Sorted by agent slug for deterministic output.
    """
    round_dir_name = f"round{round_num}"
    candidates: list[Path] = []
    for agent_dir in sorted(competition_root.glob("agent-*")):
        submission_dir = agent_dir / round_dir_name
        if not submission_dir.is_dir():
            continue
        if (submission_dir / "strategy.py").is_file():
            candidates.append(submission_dir)
            continue
        # Per-pair layout: submission_dir has no top-level strategy.py,
        # but any direct child directory may contain one.
        for child in submission_dir.iterdir():
            if child.is_dir() and (child / "strategy.py").is_file():
                candidates.append(submission_dir)
                break
    return candidates


def detect_submission_layout(submission_dir: Path) -> tuple[str, list[Path]]:
    """Classify ``submission_dir`` as ``monolithic`` or ``per_pair``.

    Returns a ``(layout, pair_dirs)`` tuple:

    * ``("monolithic", [submission_dir])`` — the directory contains
      ``strategy.py`` at the top level. ``pair_dirs`` is a singleton list
      with the same path for consistency with the per-pair shape.
    * ``("per_pair", [pair_dir1, pair_dir2, ...])`` — the directory has
      no top-level ``strategy.py`` but every direct child directory that
      contains a ``strategy.py`` is treated as an independent submission.
      ``pair_dirs`` is sorted by directory name for deterministic output.

    Raises ``ValueError`` on mixed layouts (both a top-level
    ``strategy.py`` and a subdirectory containing one), on empty
    directories, and on per-pair directories where at least one subdir is
    missing its own ``strategy.py`` (partial per-pair submissions are
    rejected so agents can't ship a half-wired portfolio).
    """
    if not submission_dir.is_dir():
        raise ValueError(f"Not a directory: {submission_dir}")

    top_strategy = (submission_dir / "strategy.py").is_file()
    subdirs = sorted(
        (p for p in submission_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    subdirs_with_strategy = [p for p in subdirs if (p / "strategy.py").is_file()]

    if top_strategy and subdirs_with_strategy:
        raise ValueError(
            f"Mixed submission layout at {submission_dir}: both a top-level "
            f"strategy.py and per-pair subdirectories with strategy.py exist. "
            f"Pick one layout."
        )

    if top_strategy:
        return ("monolithic", [submission_dir])

    if not subdirs_with_strategy:
        raise ValueError(
            f"No strategy.py found at {submission_dir} or in any direct subdirectory."
        )

    # All subdirs that look like pair submissions must have strategy.py.
    # A subdir without strategy.py is treated as unrelated (e.g. `__pycache__`,
    # `notes/`, `shared/`) only if it is obviously not a pair dir. We
    # enforce the stricter rule: any subdir whose name looks like a pair
    # (upper-case, ends in USDT, contains only alnum + dot) must contain
    # strategy.py. Non-pair-like subdirs (e.g. ``__pycache__``) are ignored.
    pair_like_missing: list[str] = []
    for p in subdirs:
        if p in subdirs_with_strategy:
            continue
        name = p.name
        if name.startswith("_") or name.startswith("."):
            continue
        upper = name.upper()
        if upper == name and upper.endswith("USDT"):
            pair_like_missing.append(name)
    if pair_like_missing:
        raise ValueError(
            f"Per-pair submission at {submission_dir} is incomplete: "
            f"subdirs {pair_like_missing!r} look like pair dirs but lack strategy.py."
        )

    return ("per_pair", subdirs_with_strategy)


def agent_slug_from_path(submission_dir: Path) -> str:
    """Infer a short agent slug from the submission path.

    For ``competition/agent-5-hybrid/round11`` returns ``agent5_hybrid``. For
    ad-hoc paths (e.g. ``competition/TEMPLATE``), falls back to the parent
    directory name lowercased.
    """
    parts = submission_dir.parts
    for part in reversed(parts):
        if part.startswith("agent-"):
            stripped = part.removeprefix("agent-")
            pieces = stripped.split("-", 1)
            if pieces[0].isdigit() and len(pieces) == 2:
                return f"agent{pieces[0]}_{pieces[1]}"
            return stripped.replace("-", "_")
    return submission_dir.name.lower()


# ---------------------------------------------------------------------------
# Strategy loading
# ---------------------------------------------------------------------------


def load_strategy_module(strategy_path: Path) -> ModuleType:
    """Import ``strategy.py`` under a unique module name.

    Using ``id(strategy_path)`` (as the validator does) avoids cache
    collisions when the evaluator and validator both load different copies
    of the same filename within one Python process.
    """
    unique_name = f"competition_r11_submission_{id(strategy_path)}"
    spec = importlib.util.spec_from_file_location(unique_name, strategy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to build import spec for {strategy_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as err:
        sys.modules.pop(unique_name, None)
        raise ImportError(f"Failed to exec {strategy_path}: {err}") from err
    return module


def read_manifest(module: ModuleType) -> dict[str, Any]:
    """Return the submission's MANIFEST dict, raising on malformed input."""
    manifest = getattr(module, "MANIFEST", None)
    if not isinstance(manifest, dict):
        raise ValueError("strategy.py does not expose a module-level MANIFEST dict")
    missing = [k for k in REQUIRED_MANIFEST_KEYS if k not in manifest]
    if missing:
        raise ValueError(f"MANIFEST missing required keys: {missing}")
    return manifest


# ---------------------------------------------------------------------------
# Real-data loaders (NO synthetic fallbacks)
# ---------------------------------------------------------------------------


def load_catalog_bars(
    catalog_path: Path,
    bar_type: BarType,
    instrument_id: InstrumentId,
) -> tuple[list[Bar], Any | None]:
    """Load bars + instrument from a ``ParquetDataCatalog``. Raise on miss.

    Returns ``(bars, instrument)`` where bars is non-empty and instrument
    may be ``None`` if the catalog stored only bars (the caller can build
    a default instrument in that case).

    Raises ``EvalDataError`` if the catalog cannot be opened, contains no
    bars matching ``(instrument_id, bar_type)``, or any other lookup
    failure. Callers must NEVER substitute synthetic data on this path.
    """
    try:
        catalog = ParquetDataCatalog(str(catalog_path))
    except Exception as err:
        raise EvalDataError(
            f"Could not open ParquetDataCatalog at {catalog_path}: {err}"
        ) from err

    instrument = None
    try:
        for inst in catalog.instruments():
            if inst.id == instrument_id:
                instrument = inst
                break
    except Exception as err:
        raise EvalDataError(
            f"Catalog instrument lookup failed at {catalog_path} for "
            f"{instrument_id}: {err}"
        ) from err

    try:
        bars = catalog.bars(instrument_ids=[str(instrument_id)])
    except Exception as err:
        raise EvalDataError(
            f"Catalog bar lookup failed at {catalog_path} for "
            f"{instrument_id}: {err}"
        ) from err

    filtered = [b for b in bars if b.bar_type == bar_type]
    if not filtered:
        raise EvalDataError(
            f"Catalog at {catalog_path} contains 0 bars matching "
            f"{bar_type} (instrument {instrument_id}). Repopulate the "
            f"catalog or omit --catalog-path to fetch from Binance."
        )
    return filtered, instrument


def fetch_binance_bars(
    *,
    symbol: str,
    bar_type: BarType,
    start: str,
    end: str,
    price_precision: int,
    size_precision: int,
    warmup_days: int = 30,
) -> list[Bar]:
    """Download 1-hour klines from Binance public API. Raise on failure.

    ``symbol`` is the bare Binance symbol (e.g. ``"BTCUSDT"``). ``start``
    and ``end`` are ``YYYY-MM-DD`` strings (UTC). A ``warmup_days``-day
    buffer is prepended so indicators can warm up before the eval window.

    Raises ``EvalDataError`` on HTTP failure or empty response. NEVER
    falls back to synthetic data.
    """
    try:
        import requests
    except ImportError as err:
        raise EvalDataError(
            "requests is not installed (uv add requests)"
        ) from err

    warmup_start = (
        datetime.strptime(start, "%Y-%m-%d") - timedelta(days=warmup_days)
    ).strftime("%Y-%m-%d")

    start_ms = int(
        datetime.strptime(warmup_start, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
        * 1000
    )
    end_ms = int(
        datetime.strptime(end, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
        * 1000
    ) + 86_400_000

    url = "https://api.binance.com/api/v3/klines"
    raw: list[list] = []
    cur = start_ms

    logger.info(
        "Fetching %s 1h bars from Binance (%s to %s)...", symbol, warmup_start, end
    )
    while cur < end_ms:
        try:
            resp = requests.get(
                url,
                params={
                    "symbol": symbol,
                    "interval": "1h",
                    "startTime": cur,
                    "endTime": end_ms,
                    "limit": 1000,
                },
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as err:
            raise EvalDataError(
                f"Binance klines request failed for {symbol} "
                f"({warmup_start} -> {end}): {err}"
            ) from err
        batch = resp.json()
        if not batch:
            break
        raw.extend(batch)
        last = batch[-1][0]
        if last <= cur or len(batch) < 1000:
            break
        cur = last + 1
        time.sleep(0.12)

    if not raw:
        raise EvalDataError(
            f"Binance klines returned 0 bars for {symbol} "
            f"from {warmup_start} to {end}."
        )

    bars: list[Bar] = []
    for k in raw:
        ts_init = k[0] * 1_000_000  # open time ms -> ns
        ts_event = k[6] * 1_000_000  # close time ms -> ns
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price(float(k[1]), price_precision),
                high=Price(float(k[2]), price_precision),
                low=Price(float(k[3]), price_precision),
                close=Price(float(k[4]), price_precision),
                volume=Quantity(float(k[5]), size_precision),
                ts_event=ts_event,
                ts_init=ts_init,
            )
        )
    logger.info("Fetched %d bars for %s", len(bars), symbol)
    return bars


def load_bars_real(
    *,
    ctx: EvalContext,
    instrument_id: InstrumentId,
    bar_type: BarType,
    instrument: Any,
) -> tuple[list[Bar], Any]:
    """Choose between catalog and Binance fetch. Real data only -- no synthetic.

    * ``ctx.catalog_path`` set + ``ctx.require_catalog`` true: catalog only,
      raise on miss.
    * ``ctx.catalog_path`` set + ``ctx.require_catalog`` false: try catalog
      first, fall through to Binance fetch on ``EvalDataError``.
    * ``ctx.catalog_path`` unset: Binance fetch directly.

    The returned ``instrument`` is the catalog-stored one when available
    (preserves price/size precision); otherwise the supplied one.
    """
    if ctx.catalog_path is not None:
        try:
            bars, cat_instrument = load_catalog_bars(
                ctx.catalog_path, bar_type, instrument_id
            )
            if cat_instrument is not None:
                instrument = cat_instrument
            return bars, instrument
        except EvalDataError:
            if ctx.require_catalog:
                raise
            logger.info(
                "Catalog miss for %s/%s; falling through to Binance fetch",
                instrument_id,
                bar_type,
            )

    symbol = instrument_id.symbol.value
    bars = fetch_binance_bars(
        symbol=symbol,
        bar_type=bar_type,
        start=ctx.eval_start,
        end=ctx.eval_end,
        price_precision=instrument.price_precision,
        size_precision=instrument.size_precision,
    )
    return bars, instrument


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------


def _extract_float(value: Any) -> float:
    """Coerce an analyzer stat to a float, treating NaN/None/strings as 0."""
    if value is None:
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(f) or math.isinf(f):
        return 0.0
    return f


def _compute_max_drawdown_pct(returns_series: Any, starting_capital: Decimal) -> float:
    """Compute max drawdown % from a pandas Series of per-trade returns.

    Walks a synthetic equity curve (starting_capital + cumulative returns)
    and returns the largest peak-to-trough drop as a percentage (negative
    number, e.g. ``-12.34``). Returns 0.0 if there are fewer than 2 samples.
    """
    if returns_series is None or len(returns_series) < 2:
        return 0.0
    running_max = float(starting_capital)
    equity = float(starting_capital)
    max_dd = 0.0
    for r in returns_series.values:
        r_float = float(r)
        if math.isnan(r_float) or math.isinf(r_float):
            continue
        equity = equity * (1.0 + r_float)
        if equity > running_max:
            running_max = equity
        if running_max > 0:
            dd = (equity - running_max) / running_max * 100.0
            if dd < max_dd:
                max_dd = dd
    return round(max_dd, 4)


def extract_metrics(
    engine: BacktestEngine,
    starting_capital: Decimal,
) -> dict[str, Any]:
    """Pull final equity, return %, Sharpe, win rate, and drawdown from engine."""
    account = engine.cache.account_for_venue(BINANCE_VENUE)
    balances = account.balances_total() if account is not None else {}
    usdt_balance = balances.get(USDT)
    final_equity = (
        Decimal(str(usdt_balance.as_decimal())) if usdt_balance is not None else starting_capital
    )

    # Add any residual (e.g. still-open) PnL in target currency USDT.
    try:
        residual = engine.portfolio.total_pnls(BINANCE_VENUE, target_currency=USDT)
        residual_money = residual.get(USDT) if residual else None
        if residual_money is not None:
            residual_dec = Decimal(str(residual_money.as_decimal()))
            open_only = residual_dec - (final_equity - starting_capital)
            if abs(open_only) > Decimal("0"):
                final_equity += open_only
    except Exception as err:  # pragma: no cover - defensive
        logger.debug("total_pnls residual calc failed: %s", err)

    total_return_pct = 0.0
    if starting_capital > 0:
        diff = final_equity - starting_capital
        total_return_pct = float(diff / starting_capital * Decimal("100"))

    analyzer = engine.portfolio.analyzer
    pnl_stats: dict[str, Any] = {}
    returns_stats: dict[str, Any] = {}
    try:
        pnl_stats = analyzer.get_performance_stats_pnls(USDT)
    except Exception as err:
        logger.debug("get_performance_stats_pnls failed: %s", err)
    try:
        returns_stats = analyzer.get_performance_stats_returns()
    except Exception as err:
        logger.debug("get_performance_stats_returns failed: %s", err)

    sharpe = _extract_float(returns_stats.get("Sharpe Ratio (252 days)"))
    win_rate_fraction = _extract_float(pnl_stats.get("Win Rate"))
    win_rate_pct = win_rate_fraction * 100.0 if win_rate_fraction <= 1.0 else win_rate_fraction

    max_dd_pct = _compute_max_drawdown_pct(analyzer.returns(), starting_capital)

    num_trades = int(engine.cache.positions_closed_count() or 0)

    return {
        "final_equity": final_equity.quantize(Decimal("0.01")),
        "total_return_pct": round(total_return_pct, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": max_dd_pct,
        "num_trades": num_trades,
        "win_rate_pct": round(win_rate_pct, 2),
    }


# ---------------------------------------------------------------------------
# Single-submission evaluation
# ---------------------------------------------------------------------------


def evaluate_submission(submission_dir: Path, ctx: EvalContext) -> SubmissionResult:
    """Validate, load, and run one submission. Always returns a result.

    Detects whether the submission uses the monolithic layout
    (``<dir>/strategy.py``) or the per-pair layout
    (``<dir>/<PAIR>/strategy.py``). For per-pair agents each pair is
    evaluated independently with ``initial_capital / N_pairs`` of the
    budget, then the per-pair ``SubmissionResult``s are aggregated into a
    single agent-level result.
    """
    agent_slug = agent_slug_from_path(submission_dir)
    try:
        layout, pair_dirs = detect_submission_layout(submission_dir)
    except ValueError as err:
        return SubmissionResult(
            agent_slug=agent_slug,
            submission_dir=submission_dir,
            status="INVALID",
            error=str(err),
            final_equity=ctx.initial_capital,
        )

    if layout == "monolithic":
        return _evaluate_pair(
            submission_dir,
            ctx,
            capital=ctx.initial_capital,
            agent_slug=agent_slug,
        )

    # --- per-pair layout ---
    n_pairs = len(pair_dirs)
    if n_pairs == 0:  # pragma: no cover - detect_submission_layout rejects first
        return SubmissionResult(
            agent_slug=agent_slug,
            submission_dir=submission_dir,
            status="INVALID",
            error="per-pair submission has no pair directories",
            final_equity=ctx.initial_capital,
        )

    # Equal capital split. Quantize down so the sum never exceeds the
    # starting budget (any leftover fraction of a cent is discarded).
    capital_per_pair = (ctx.initial_capital / Decimal(n_pairs)).quantize(
        Decimal("0.01"),
    )

    pair_results: list[SubmissionResult] = []
    for pair_dir in pair_dirs:
        pair_slug = f"{agent_slug}::{pair_dir.name}"
        pair_results.append(
            _evaluate_pair(
                pair_dir,
                ctx,
                capital=capital_per_pair,
                agent_slug=pair_slug,
            ),
        )

    return _aggregate_pair_results(
        agent_slug=agent_slug,
        submission_dir=submission_dir,
        pair_results=pair_results,
        total_capital=ctx.initial_capital,
    )


def _evaluate_pair(
    submission_dir: Path,
    ctx: EvalContext,
    *,
    capital: Decimal,
    agent_slug: str,
) -> SubmissionResult:
    """Evaluate a single pluggable submission directory.

    ``capital`` is the starting USDT balance for this pair's engine (for
    monolithic submissions it equals ``ctx.initial_capital``; for per-pair
    agents it's the pair's slice of the portfolio). ``agent_slug`` is the
    label used in logs and in the returned ``SubmissionResult``.
    """
    result = SubmissionResult(
        agent_slug=agent_slug,
        submission_dir=submission_dir,
        status="ERROR",
        final_equity=capital,
    )

    # --- Step 1: validate ---
    try:
        report = validate(submission_dir)
    except Exception as err:
        result.status = "INVALID"
        result.error = f"validator crashed: {err}"
        logger.error("Validator crashed on %s: %s", submission_dir, err)
        return result

    if report.has_failures():
        failed_titles = [
            f"step{r.step} {r.title}" for r in report.results if r.status == "FAIL"
        ]
        result.status = "INVALID"
        result.error = "; ".join(failed_titles) or "validation failed"
        logger.warning("Submission %s failed validation: %s", agent_slug, result.error)
        return result

    # --- Step 2: load strategy module + manifest ---
    strategy_path = submission_dir / "strategy.py"
    try:
        module = load_strategy_module(strategy_path)
        manifest = read_manifest(module)
    except Exception as err:
        result.status = "ERROR"
        result.error = f"load/manifest: {err}"
        logger.error("Failed to load %s: %s", submission_dir, err)
        return result

    result.description = str(manifest.get("description", ""))
    strategy_class_name = str(manifest["strategy_class_name"])
    config_class_name = str(manifest["config_class_name"])
    result.strategy_name = strategy_class_name

    strategy_cls = getattr(module, strategy_class_name, None)
    config_cls = getattr(module, config_class_name, None)
    if strategy_cls is None or config_cls is None:
        result.status = "ERROR"
        result.error = (
            f"classes not found in module: {strategy_class_name!r}, {config_class_name!r}"
        )
        return result

    # --- Step 3: parse identifiers ---
    try:
        instrument_id = InstrumentId.from_str(str(manifest["instrument_id"]))
        bar_type = BarType.from_str(str(manifest["bar_type"]))
    except Exception as err:
        result.status = "ERROR"
        result.error = f"invalid instrument_id/bar_type: {err}"
        return result

    if ctx.allowlist and str(instrument_id) not in ctx.allowlist:
        result.status = "INVALID"
        result.error = (
            f"instrument {instrument_id} not in round {ctx.round_num} allowlist"
        )
        return result

    # --- Step 4: build engine, instrument, data ---
    try:
        engine = _build_engine(capital)
    except Exception as err:
        result.status = "ERROR"
        result.error = f"engine build failed: {err}"
        return result

    try:
        instrument = _build_crypto_instrument(instrument_id.symbol.value, ts_now_ns=0)
    except Exception as err:
        result.status = "ERROR"
        result.error = f"instrument build failed: {err}"
        engine.dispose()
        return result

    # Real-data load. Raises EvalDataError on miss; never falls back to synthetic.
    # If a catalog is supplied and exposes its own instrument with matching id,
    # load_bars_real swaps it in so price/size precision lines up with the bars.
    try:
        bars, instrument = load_bars_real(
            ctx=ctx,
            instrument_id=instrument_id,
            bar_type=bar_type,
            instrument=instrument,
        )
    except EvalDataError as err:
        result.status = "ERROR"
        result.error = f"data unavailable: {err}"
        logger.error("Data load failed for %s: %s", agent_slug, err)
        engine.dispose()
        return result

    engine.add_instrument(instrument)
    engine.add_data(bars)

    # --- Step 5: instantiate strategy ---
    default_cfg = dict(manifest.get("default_config") or {})
    default_cfg.pop("instrument_id", None)
    default_cfg.pop("bar_type", None)
    try:
        config = config_cls(
            instrument_id=instrument_id,
            bar_type=bar_type,
            **default_cfg,
        )
        strategy = strategy_cls(config=config)
    except Exception as err:
        result.status = "ERROR"
        result.error = f"strategy instantiation failed: {err}"
        engine.dispose()
        return result

    engine.add_strategy(strategy)

    # --- Step 6: run + extract ---
    try:
        engine.run()
    except Exception as err:
        result.status = "ERROR"
        result.error = f"engine.run() raised: {err}"
        logger.error("engine.run() failed for %s:\n%s", agent_slug, traceback.format_exc())
        engine.dispose()
        return result

    try:
        metrics = extract_metrics(engine, capital)
    except Exception as err:
        result.status = "ERROR"
        result.error = f"metric extraction failed: {err}"
        engine.dispose()
        return result

    engine.dispose()

    result.status = "OK"
    result.final_equity = metrics["final_equity"]
    result.total_return_pct = metrics["total_return_pct"]
    result.sharpe_ratio = metrics["sharpe_ratio"]
    result.max_drawdown_pct = metrics["max_drawdown_pct"]
    result.num_trades = metrics["num_trades"]
    result.win_rate_pct = metrics["win_rate_pct"]
    return result


def _aggregate_pair_results(
    *,
    agent_slug: str,
    submission_dir: Path,
    pair_results: list[SubmissionResult],
    total_capital: Decimal,
) -> SubmissionResult:
    """Fold per-pair ``SubmissionResult``s into a single agent-level result.

    Aggregation rules (see task brief):

    * ``final_equity`` = sum of per-pair final equities. Per-pair
      engines each start with an equal slice of the total capital and
      evolve independently, so the portfolio equity at the end of the
      window is simply their sum.
    * ``total_return_pct`` = capital-weighted sum of per-pair returns.
      With equal weights this is the arithmetic mean but we implement
      the general form so a future smarter allocator (Q19) drops in
      without rewriting the math.
    * ``sharpe_ratio`` = capital-weighted average across OK pairs.
    * ``num_trades`` = sum across pairs.
    * ``max_drawdown_pct`` = most negative per-pair drawdown. Without
      correlation data between pairs we cannot compute a true portfolio
      drawdown, so we fall back to the conservative worst-pair estimate.
    * ``win_rate_pct`` = trade-weighted average across OK pairs (so
      zero-trade pairs don't pull the blended win rate down to 0).

    If any pair has a non-OK status, the aggregate is tagged as INVALID
    and the error list enumerates the per-pair failures. The caller sees
    a single actionable failure instead of a confusing partial
    leaderboard row.
    """
    ok_pairs = [r for r in pair_results if r.status == "OK"]
    bad_pairs = [r for r in pair_results if r.status != "OK"]

    # Strategy label + description for the agent row: prefer the first
    # successful pair if any, else fall back to the first broken one so
    # the INVALID block still names a real class.
    first_ok = ok_pairs[0] if ok_pairs else None
    first_any = first_ok or pair_results[0]
    description = f"per-pair portfolio over {len(pair_results)} pairs"
    if first_any.description:
        description = f"{description}: {first_any.description}"

    aggregate = SubmissionResult(
        agent_slug=agent_slug,
        submission_dir=submission_dir,
        status="OK",
        strategy_name=first_any.strategy_name or "per-pair",
        description=description,
        final_equity=Decimal("0.00"),
        per_pair_results=pair_results,
    )

    if bad_pairs:
        aggregate.status = "INVALID"
        errors = []
        for r in bad_pairs:
            label = r.submission_dir.name
            errors.append(f"{label}[{r.status}]: {r.error or 'unknown'}")
        aggregate.error = "; ".join(errors)
        aggregate.final_equity = total_capital
        return aggregate

    if not ok_pairs:  # pragma: no cover - bad_pairs covers this
        aggregate.status = "INVALID"
        aggregate.error = "no pairs evaluated"
        aggregate.final_equity = total_capital
        return aggregate

    total_final_equity = sum(
        (r.final_equity for r in ok_pairs),
        Decimal("0"),
    )

    # Weights: each pair's share of the starting capital. For now every
    # pair gets 1/N — a future portfolio manager could hand back uneven
    # weights and this function would not need to change.
    weight = 1.0 / len(ok_pairs)

    weighted_return = sum(r.total_return_pct * weight for r in ok_pairs)
    weighted_sharpe = sum(r.sharpe_ratio * weight for r in ok_pairs)

    total_trades = sum(r.num_trades for r in ok_pairs)
    if total_trades > 0:
        weighted_win_rate = sum(
            r.win_rate_pct * (r.num_trades / total_trades) for r in ok_pairs
        )
    else:
        weighted_win_rate = 0.0

    # Worst-case drawdown: most negative per-pair number. (``min`` on a
    # list of negative percentages returns the largest absolute value.)
    worst_drawdown = min((r.max_drawdown_pct for r in ok_pairs), default=0.0)

    aggregate.final_equity = total_final_equity.quantize(Decimal("0.01"))
    aggregate.total_return_pct = round(weighted_return, 4)
    aggregate.sharpe_ratio = round(weighted_sharpe, 4)
    aggregate.max_drawdown_pct = round(worst_drawdown, 4)
    aggregate.num_trades = total_trades
    aggregate.win_rate_pct = round(weighted_win_rate, 2)
    return aggregate


def _build_engine(initial_capital: Decimal) -> BacktestEngine:
    """Return a BacktestEngine preconfigured for Binance Spot + USDT base."""
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
        ),
    )
    engine.add_venue(
        venue=BINANCE_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(initial_capital, USDT)],
    )
    return engine


# ---------------------------------------------------------------------------
# Result rendering
# ---------------------------------------------------------------------------


def render_results(
    results: list[SubmissionResult],
    ctx: EvalContext,
    generated_at: datetime,
) -> str:
    """Render the leaderboard table in a style similar to R10 output."""
    ok_results = [r for r in results if r.status == "OK"]
    invalid_results = [r for r in results if r.status != "OK"]
    ok_results.sort(key=lambda r: r.total_return_pct, reverse=True)

    lines: list[str] = []
    lines.append(f"Round {ctx.round_num} Results")
    lines.append("=" * 40)
    lines.append(f"Generated: {generated_at.isoformat()}")
    lines.append(f"Eval period: {ctx.eval_start} -> {ctx.eval_end}")
    lines.append(f"Initial capital: ${ctx.initial_capital:.2f} USDT")
    lines.append("")

    header = (
        "| Rank | Agent            | Strategy            "
        "| Final Equity   | Return %   | Sharpe   | Max DD %   | Trades | Win Rate |"
    )
    sep = (
        "|------|------------------|---------------------"
        "|----------------|------------|----------|------------|--------|----------|"
    )
    lines.append(header)
    lines.append(sep)

    if not ok_results:
        lines.append(
            "| -    | (no valid submissions) |                    "
            "|                |            |          |            |        |          |"
        )

    for rank, r in enumerate(ok_results, start=1):
        agent_col = r.agent_slug[:16].ljust(16)
        strat_col = r.strategy_name[:19].ljust(19)
        equity_col = f"${r.final_equity:.2f}".rjust(14)
        return_col = f"{r.total_return_pct:+.2f}%".rjust(10)
        sharpe_col = f"{r.sharpe_ratio:.2f}".rjust(8)
        dd_col = f"{r.max_drawdown_pct:+.2f}%".rjust(10)
        trades_col = str(r.num_trades).rjust(6)
        wr_col = f"{r.win_rate_pct:.1f}%".rjust(8)
        lines.append(
            f"| {str(rank).ljust(4)} | {agent_col} | {strat_col} "
            f"| {equity_col} | {return_col} | {sharpe_col} | {dd_col} | {trades_col} | {wr_col} |"
        )

    if invalid_results:
        lines.append("")
        lines.append("INVALID submissions:")
        for r in invalid_results:
            err = r.error or r.status
            lines.append(f"- {r.agent_slug}: [{r.status}] {err}")

    winner = ok_results[0] if ok_results else None
    lines.append("")
    if winner is not None and winner.total_return_pct > 0:
        lines.append(
            f"WINNER: {winner.agent_slug} ({winner.strategy_name}) "
            f"with {winner.total_return_pct:+.2f}% return"
        )
    else:
        lines.append("NO WINNER: no agent achieved a positive return")

    lines.append("")
    lines.append("Detailed results:")
    lines.append("=" * 40)
    for r in results:
        lines.append("")
        lines.append(f"{r.agent_slug} [{r.status}]")
        lines.append("-" * 40)
        lines.append(f"  submission_dir: {r.submission_dir}")
        if r.description:
            lines.append(f"  description: {r.description}")
        if r.status == "OK":
            lines.append(f"  strategy: {r.strategy_name}")
            lines.append(f"  final_equity: {r.final_equity}")
            lines.append(f"  total_return_pct: {r.total_return_pct:+.4f}")
            lines.append(f"  sharpe_ratio: {r.sharpe_ratio:.4f}")
            lines.append(f"  max_drawdown_pct: {r.max_drawdown_pct:+.4f}")
            lines.append(f"  num_trades: {r.num_trades}")
            lines.append(f"  win_rate_pct: {r.win_rate_pct:.2f}")
        else:
            lines.append(f"  error: {r.error}")
        if r.per_pair_results:
            lines.append("  per-pair:")
            for pair in r.per_pair_results:
                pair_name = pair.submission_dir.name
                if pair.status == "OK":
                    lines.append(
                        f"    {pair_name}: return={pair.total_return_pct:+.2f}%, "
                        f"sharpe={pair.sharpe_ratio:.2f}, "
                        f"trades={pair.num_trades}, "
                        f"mdd={pair.max_drawdown_pct:+.2f}%"
                    )
                else:
                    lines.append(
                        f"    {pair_name}: [{pair.status}] {pair.error or 'unknown'}"
                    )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the evaluator."""
    parser = argparse.ArgumentParser(
        prog="evaluate_round11",
        description="Evaluate R11+ crypto competition submissions.",
    )
    parser.add_argument("--round", type=int, required=True, help="Round number (e.g. 11)")
    parser.add_argument(
        "--submission-dir",
        type=Path,
        default=None,
        help="Evaluate a single submission directory instead of discovering agent-*/round{N}/",
    )
    parser.add_argument(
        "--catalog-path",
        type=Path,
        default=None,
        help="Optional ParquetDataCatalog directory for historical bar data.",
    )
    parser.add_argument(
        "--require-catalog",
        action="store_true",
        help=(
            "If set, --catalog-path is the only allowed data source. "
            "On catalog miss the evaluator raises EvalDataError instead of "
            "falling through to a Binance fetch. Use for hermetic test runs."
        ),
    )
    parser.add_argument(
        "--eval-start",
        type=str,
        default=None,
        help="Override eval window start (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--eval-end",
        type=str,
        default=None,
        help="Override eval window end (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=None,
        help="Override starting capital in USDT (default 1000.00).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Override output path (default competition/round{N}_results.txt).",
    )
    return parser.parse_args(argv)


def build_context(args: argparse.Namespace) -> EvalContext:
    """Translate CLI args + ROUND_CONFIG into an EvalContext."""
    default_period = ROUND_CONFIG["eval_period"]
    default_capital = ROUND_CONFIG["initial_capital_usdt"]
    allowlist = tuple(ROUND_CONFIG.get("instruments_allowlist") or ())

    initial_capital = default_capital
    if args.initial_capital is not None:
        initial_capital = Decimal(str(args.initial_capital)).quantize(Decimal("0.01"))

    if args.require_catalog and args.catalog_path is None:
        raise SystemExit(
            "--require-catalog set but --catalog-path is missing. "
            "Hermetic mode needs a real on-disk catalog to read from."
        )

    return EvalContext(
        round_num=args.round,
        eval_start=args.eval_start or default_period["start"],
        eval_end=args.eval_end or default_period["end"],
        initial_capital=initial_capital,
        catalog_path=args.catalog_path,
        require_catalog=bool(args.require_catalog),
        allowlist=allowlist,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns 0 on success (results written), 2 on CLI misuse, 1 if nothing
    could be evaluated.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args = parse_args(argv)
    ctx = build_context(args)

    competition_root = _REPO_ROOT / "competition"
    submissions: list[Path]
    if args.submission_dir is not None:
        submission_dir = args.submission_dir.resolve()
        if not submission_dir.is_dir():
            logger.error("Not a directory: %s", submission_dir)
            return 2
        # Accept both monolithic (top-level strategy.py) and per-pair
        # (subdir/strategy.py) layouts. detect_submission_layout will
        # raise if neither is present.
        try:
            detect_submission_layout(submission_dir)
        except ValueError as err:
            logger.error("%s", err)
            return 2
        submissions = [submission_dir]
    else:
        submissions = discover_submissions(competition_root, ctx.round_num)
        if not submissions:
            logger.error(
                "No submissions found under %s/agent-*/round%s/",
                competition_root,
                ctx.round_num,
            )
            return 1

    results: list[SubmissionResult] = []
    for sub in submissions:
        logger.info("Evaluating %s", sub)
        results.append(evaluate_submission(sub, ctx))

    generated_at = datetime.now(tz=timezone.utc)
    rendered = render_results(results, ctx, generated_at)

    output_path = args.output or (competition_root / f"round{ctx.round_num}_results.txt")
    output_path.write_text(rendered, encoding="utf-8")

    # Print ONLY the leaderboard to stdout (logging goes to stderr).
    print(rendered)
    logger.info("Wrote %s", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
