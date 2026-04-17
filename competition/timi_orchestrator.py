"""TiMi evolution-loop orchestrator (one competition round).

Wires the four TiMi agents (A_ma → A_sa → A_be ↔ A_fr) together for a
single competition round. Reads ``round_configs/round<N>.py``, spawns each agent
in turn via ``claude -p`` (or a stub for tests), parses A_fr's refinement
directives in the format defined by
``docs/research/timi/DIRECTIVE_FORMAT.md``, hands them back to A_be, and
stops when the per-pair loop converges, diverges, or hits the budget /
max-iter cap.

This module is the **glue layer** for the evolution loop. It does NOT
score the eval window itself — that is the evaluator's job. It does not
write trading code — that is A_be's job. It only schedules the agents,
parses their handoff artifacts, runs the local validator + per-pair
backtest between iterations, and writes a per-round summary report.

Usage::

    cd /Users/rc/Projects/workspace/nautilus-trading
    uv --project nautilus run python competition/timi_orchestrator.py \\
        --round 11 --dry-run

For tests, every Claude invocation is routed through ``StubInvoker`` so
no actual ``claude -p`` subprocess fires in CI.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

# Make sibling competition modules + the nautilus src tree importable when
# this file is run directly. Mirrors the pattern in evaluate.py and
# validate_submission.py so the orchestrator stays runnable as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_NAUTILUS_SRC = _REPO_ROOT / "nautilus" / "src"
_COMPETITION_DIR = Path(__file__).resolve().parent
if str(_NAUTILUS_SRC) not in sys.path:
    sys.path.insert(0, str(_NAUTILUS_SRC))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_COMPETITION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPETITION_DIR))


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


PROMPTS_DIR: Path = _REPO_ROOT / "docs" / "research" / "timi" / "prompts"
MACRO_DIR: Path = _REPO_ROOT / "docs" / "research" / "timi" / "macro"
ADAPTED_DIR: Path = _REPO_ROOT / "docs" / "research" / "timi" / "adapted"
REFLECTION_DIR: Path = _REPO_ROOT / "docs" / "research" / "timi" / "reflection"
TIMI_AGENT_ROOT: Path = _REPO_ROOT / "competition" / "agent-6-timi"
TEMPLATE_DIR: Path = _REPO_ROOT / "competition" / "TEMPLATE"

# Stopping rule defaults (per OPEN_QUESTIONS Q13 + a_fr.md):
#   - max 5 iterations per (round, pair)
#   - converged once Δ J(π_Θ) < 0.5% for 2 consecutive iterations
#   - diverged once cumulative LLM cost for the pair exceeds $5
MAX_ITERS_PER_PAIR: int = 5
CONVERGENCE_DELTA_PCT: Decimal = Decimal("0.5")
TOKEN_BUDGET_USD_PER_PAIR: Decimal = Decimal("5.00")

# Sentinels each agent emits on success. Mirrors the prompt files exactly.
A_MA_SENTINEL: str = "MACRO_ANALYSIS_COMPLETE"
A_SA_SENTINEL: str = "STRATEGY_ADAPTATION_COMPLETE"
A_BE_SENTINEL: str = "BOT_WRITTEN_AND_VALIDATED"
A_FR_SENTINEL: str = "REFLECTION_EMITTED"

# Outcome strings used in the public dataclasses.
OUTCOME_CONVERGED: str = "converged"
OUTCOME_DIVERGED: str = "diverged"
OUTCOME_BUDGET_EXHAUSTED: str = "budget_exhausted"
OUTCOME_MAX_ITERS: str = "max_iters"
OUTCOME_VALIDATOR_FAILED: str = "validator_failed"
OUTCOME_DIRECTIVE_MALFORMED: str = "directive_malformed"
OUTCOME_HUMAN_HOLD: str = "human_review_required"


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IterationRecord:
    """One A_be ↔ A_fr round-trip for a single pair.

    ``backtest_metric`` is the per-iter pair-level total return % captured
    from ``_evaluate_pair``; it is ``None`` only when the iteration ended
    in a hard failure before the backtest could run.

    ``directive_layer`` and ``verdict`` come from the parsed A_fr
    directive; both are ``None`` if A_fr never emitted (e.g. validator
    failure on the A_be step).

    ``token_cost_usd`` accumulates A_be + A_fr costs for this single
    iteration only — caller sums them per pair to enforce the budget.
    """

    iter: int
    agent: str  # "a_be" | "a_fr"
    backtest_metric: Decimal | None
    directive_layer: str | None  # "parameter" | "function" | "strategy"
    verdict: str | None  # "continue" | "converged" | "diverged"
    token_cost_usd: Decimal


@dataclass(frozen=True)
class PairReport:
    """Per-pair outcome from one round of the evolution loop."""

    pair: str
    iterations: int
    final_objective: Decimal
    convergence_trace: list[IterationRecord]
    strategy_path: Path
    outcome: str


@dataclass(frozen=True)
class RoundReport:
    """Round-level summary returned by ``run_round``."""

    round_number: int
    per_pair_reports: list[PairReport]
    total_token_cost_usd: Decimal
    outcome: str


# ---------------------------------------------------------------------------
# Directive dataclasses + parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DirectiveEnvelope:
    """YAML frontmatter envelope as defined in DIRECTIVE_FORMAT.md §1."""

    round: int
    pair: str
    iter: int
    layer: str  # parameter | function | strategy
    verdict: str  # continue | converged | diverged
    author: str
    timestamp: str
    human_review_required: bool


@dataclass(frozen=True)
class Directive:
    """Parsed A_fr directive (envelope + body).

    The body is left semi-structured so the orchestrator can route on
    layer without re-parsing JSON-patch / YAML swap blocks. ``patches``
    is populated only for parameter-layer directives; ``raw_body`` carries
    the full markdown for downstream consumers (A_be, audit log).
    """

    path: Path
    envelope: DirectiveEnvelope
    raw_body: str
    patches: list[dict[str, Any]] = field(default_factory=list)


class DirectiveSchemaViolation(ValueError):
    """Raised when a directive file is missing fields, malformed, or
    fails the envelope checks defined in DIRECTIVE_FORMAT.md.
    """


_FRONTMATTER_RE: re.Pattern[str] = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)$",
    re.DOTALL,
)
# Greedy/lazy JSON code fence — captures the first ```json … ``` block in
# the body. Parameter-layer directives encode their patch list this way
# (DIRECTIVE_FORMAT.md §2 + §8.1).
_JSON_FENCE_RE: re.Pattern[str] = re.compile(
    r"```json\s*\n(.*?)\n```",
    re.DOTALL,
)


def parse_directive(path: Path) -> Directive:
    """Parse an A_fr refinement directive markdown file.

    Splits the YAML frontmatter from the markdown body, validates the
    envelope against the rules in DIRECTIVE_FORMAT.md §1, and (for
    parameter-layer directives) extracts the JSON-patch body block.

    Raises ``DirectiveSchemaViolation`` on any structural issue. Callers
    catch this and report the affected pair as ``directive_malformed``.

    Notes on edge-cases handled here:

    * The frontmatter delimiter is the literal three-dash line. Trailing
      whitespace on the line is tolerated.
    * The body may contain *additional* fenced blocks (LP YAML, prose);
      we only inspect the first ``json`` block as the patch payload.
    * Older A_fr prompt drafts use ``iteration:`` instead of ``iter:`` —
      we accept either as a sliding-compatibility shim, but normalize to
      ``iter`` in the returned envelope.
    """
    if not path.is_file():
        raise DirectiveSchemaViolation(f"Directive file not found: {path}")

    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise DirectiveSchemaViolation(
            f"Missing or malformed YAML frontmatter delimited by '---' in {path}"
        )

    frontmatter_raw, body = match.group(1), match.group(2)

    try:
        frontmatter = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError as err:
        raise DirectiveSchemaViolation(
            f"YAML frontmatter parse error in {path}: {err}"
        ) from err

    if not isinstance(frontmatter, dict):
        raise DirectiveSchemaViolation(
            f"Frontmatter in {path} did not parse to a mapping"
        )

    # Sliding compatibility: allow legacy `iteration:` from a_fr.md.
    if "iter" not in frontmatter and "iteration" in frontmatter:
        frontmatter["iter"] = frontmatter.pop("iteration")

    envelope = _build_envelope(frontmatter, path)

    patches: list[dict[str, Any]] = []
    if envelope.layer == "parameter":
        patches = _extract_patches(body, path)

    return Directive(path=path, envelope=envelope, raw_body=body, patches=patches)


def _build_envelope(data: dict[str, Any], path: Path) -> DirectiveEnvelope:
    """Validate the frontmatter dict and return a typed envelope."""
    required = (
        "round",
        "pair",
        "iter",
        "layer",
        "verdict",
        "author",
        "timestamp",
        "human_review_required",
    )
    missing = [k for k in required if k not in data]
    if missing:
        raise DirectiveSchemaViolation(
            f"Envelope in {path} missing required keys: {missing}"
        )

    layer = str(data["layer"])
    if layer not in ("parameter", "function", "strategy"):
        raise DirectiveSchemaViolation(
            f"Envelope.layer in {path} must be one of parameter/function/strategy, "
            f"got {layer!r}"
        )

    verdict = str(data["verdict"])
    if verdict not in ("continue", "converged", "diverged"):
        raise DirectiveSchemaViolation(
            f"Envelope.verdict in {path} must be one of continue/converged/diverged, "
            f"got {verdict!r}"
        )

    author = str(data["author"])
    if author != "A_fr":
        raise DirectiveSchemaViolation(
            f"Envelope.author in {path} must be 'A_fr', got {author!r}"
        )

    try:
        round_num = int(data["round"])
        iter_num = int(data["iter"])
    except (TypeError, ValueError) as err:
        raise DirectiveSchemaViolation(
            f"round/iter must be integers in {path}: {err}"
        ) from err

    return DirectiveEnvelope(
        round=round_num,
        pair=str(data["pair"]),
        iter=iter_num,
        layer=layer,
        verdict=verdict,
        author=author,
        timestamp=str(data["timestamp"]),
        human_review_required=bool(data["human_review_required"]),
    )


def _extract_patches(body: str, path: Path) -> list[dict[str, Any]]:
    """Pull the first ``json`` fenced block out of a parameter directive.

    Returns a list of dicts, one per JSON-patch entry. Raises
    DirectiveSchemaViolation if the block is absent or doesn't decode to
    a list of mappings.
    """
    json_match = _JSON_FENCE_RE.search(body)
    if json_match is None:
        raise DirectiveSchemaViolation(
            f"Parameter-layer directive {path} has no ```json patch block"
        )
    try:
        decoded = json.loads(json_match.group(1))
    except json.JSONDecodeError as err:
        raise DirectiveSchemaViolation(
            f"JSON patch block in {path} failed to decode: {err}"
        ) from err
    if not isinstance(decoded, list):
        raise DirectiveSchemaViolation(
            f"JSON patch block in {path} must be an array, got {type(decoded).__name__}"
        )
    for i, entry in enumerate(decoded):
        if not isinstance(entry, dict):
            raise DirectiveSchemaViolation(
                f"JSON patch entry {i} in {path} must be an object"
            )
        for key in ("field", "old_value", "new_value", "justification", "lp_solution_ref"):
            if key not in entry:
                raise DirectiveSchemaViolation(
                    f"JSON patch entry {i} in {path} missing required key {key!r}"
                )
    return decoded


# ---------------------------------------------------------------------------
# Hidden eval window guard
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HiddenWindowGuard:
    """Prompt-level + path-level guard against eval-window leakage.

    Per OPEN_QUESTIONS Q9, the orchestrator MUST NOT pass the eval window
    date range to any agent. v0 is mechanical-light: we strip the eval
    window from any prompt context object before handing it to the
    invoker, and we expose ``redact_round_config`` so callers feed agents
    a sanitized snapshot.

    A future task can extend this to wrap parquet path access with a
    refusing reader. For now we audit the contract at the boundary.
    """

    eval_start: str
    eval_end: str

    def redact_round_config(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of ``raw`` with eval-period keys removed.

        The agents only see the round number, instruments, capital, and
        any non-eval-period fields. ``eval_period`` is replaced with a
        sentinel so any agent that walks the dict can prove it never
        saw the date range.
        """
        redacted = {k: v for k, v in raw.items() if k != "eval_period"}
        redacted["eval_period"] = "[REDACTED — orchestrator stripped]"
        return redacted

    def assert_prompt_clean(self, prompt_text: str) -> None:
        """Raise if the prompt body contains either of the eval dates.

        Cheap pattern check; the goal is to catch a copy-paste mistake
        in a templated prompt before it ever reaches an agent. Not a
        cryptographic guarantee — that's a follow-up.
        """
        for needle in (self.eval_start, self.eval_end):
            if needle in prompt_text:
                raise RuntimeError(
                    f"HiddenWindowGuard refused: prompt contains eval window date {needle!r}"
                )


# ---------------------------------------------------------------------------
# Agent invoker abstraction
# ---------------------------------------------------------------------------


@dataclass
class AgentInvocationResult:
    """Outcome of one agent invocation.

    ``stdout`` is the literal text the agent emitted on its final reply,
    used by the orchestrator to look for sentinel strings. ``cost_usd``
    is the LLM API cost the orchestrator should bill against the pair's
    budget. For ``StubInvoker`` this is always ``Decimal('0.01')`` so the
    test suite can deterministically exhaust the budget.
    """

    stdout: str
    cost_usd: Decimal


class AgentInvoker(ABC):
    """Abstract base for the four-agent fan-out.

    Concrete subclasses decide how the agent process is launched —
    ``ClaudeProcessInvoker`` shells out to ``claude -p``, ``StubInvoker``
    returns pre-baked fixture responses for tests. The orchestrator only
    talks through this interface so no real Claude calls happen in CI.
    """

    @abstractmethod
    def invoke(
        self,
        agent_name: str,
        system_prompt_path: Path,
        user_prompt: str,
    ) -> AgentInvocationResult:
        """Run one agent and return its stdout + cost.

        ``agent_name`` is the short tag (``a_ma`` / ``a_sa`` / ``a_be`` /
        ``a_fr``) used by stubs to look up the canned response.
        ``system_prompt_path`` points at the prompt file under
        ``docs/research/timi/prompts/``. ``user_prompt`` is the
        per-invocation message body (already redacted by the orchestrator).
        """

    @property
    @abstractmethod
    def last_cost_usd(self) -> Decimal:
        """Cost of the most recent invocation. Used for budget tracking."""


class ClaudeProcessInvoker(AgentInvoker):
    """Real-world invoker that shells out to ``claude -p``.

    NOT exercised in CI — every test routes through ``StubInvoker``. The
    constructor accepts the path to the ``claude`` executable so a host
    with a non-standard install can override it. The implementation is
    deliberately small: we lay out the subprocess call, capture stdout,
    and report cost as zero (Claude CLI does not currently emit a cost
    line we can parse — a follow-up task will wire that in once the CLI
    exposes it).
    """

    def __init__(self, claude_binary: str = "claude") -> None:
        self.claude_binary = claude_binary
        self._last_cost_usd: Decimal = Decimal("0.00")

    def invoke(
        self,
        agent_name: str,
        system_prompt_path: Path,
        user_prompt: str,
    ) -> AgentInvocationResult:
        # Imported lazily so the test suite never imports subprocess via
        # this module's top-level — keeps the failure mode of "ran claude
        # in CI" loud rather than silent.
        import subprocess  # noqa: PLC0415

        if not system_prompt_path.is_file():
            raise FileNotFoundError(
                f"System prompt for {agent_name} not found: {system_prompt_path}"
            )

        cmd = [
            self.claude_binary,
            "-p",
            user_prompt,
            "--system",
            str(system_prompt_path),
        ]
        logger.info("Spawning %s via %s", agent_name, " ".join(cmd[:2]))
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"claude -p exited {completed.returncode} for {agent_name}: "
                f"{completed.stderr.strip()}"
            )

        # Cost reporting is a follow-up task; record zero so the budget
        # check is conservative (test runs use the stub invoker anyway).
        self._last_cost_usd = Decimal("0.00")
        return AgentInvocationResult(stdout=completed.stdout, cost_usd=self._last_cost_usd)

    @property
    def last_cost_usd(self) -> Decimal:
        return self._last_cost_usd


@dataclass
class StubResponse:
    """One pre-baked agent reply for tests.

    ``stdout`` is the text the orchestrator will see (must contain the
    sentinel string for the agent in question). ``side_effects`` is a
    list of callables run before the response is returned — used to
    create directive files, simulate validator failures, etc.
    """

    stdout: str
    side_effects: list[Any] = field(default_factory=list)


class StubInvoker(AgentInvoker):
    """Test invoker that pops pre-baked responses from a per-agent queue.

    Tests build one of these with a dict of agent_name → list[StubResponse]
    and pass it to ``run_round`` via the ``invoker`` parameter. Each call
    pops the next response off the agent's queue and fires its side
    effects (e.g., writing a directive file under
    ``docs/research/timi/reflection/``).

    Cost is fixed at ``Decimal('0.01')`` per call so the budget logic is
    deterministic — a 500-iteration test could in principle drain the
    $5/pair cap, but our happy-path tests use < 20 calls.
    """

    def __init__(
        self,
        responses: dict[str, list[StubResponse]],
        cost_per_call: Decimal = Decimal("0.01"),
    ) -> None:
        self._responses = {name: list(queue) for name, queue in responses.items()}
        self._cost_per_call = cost_per_call
        self._last_cost_usd: Decimal = Decimal("0")
        self.invocations: list[tuple[str, Path, str]] = []

    def invoke(
        self,
        agent_name: str,
        system_prompt_path: Path,
        user_prompt: str,
    ) -> AgentInvocationResult:
        self.invocations.append((agent_name, system_prompt_path, user_prompt))
        queue = self._responses.get(agent_name)
        if not queue:
            raise RuntimeError(
                f"StubInvoker has no remaining response for agent {agent_name!r} "
                f"(call #{len(self.invocations)})"
            )
        response = queue.pop(0)
        for effect in response.side_effects:
            effect()
        self._last_cost_usd = self._cost_per_call
        return AgentInvocationResult(
            stdout=response.stdout,
            cost_usd=self._cost_per_call,
        )

    @property
    def last_cost_usd(self) -> Decimal:
        return self._last_cost_usd


# ---------------------------------------------------------------------------
# Round configuration loading
# ---------------------------------------------------------------------------


def _load_round_config(round_number: int) -> dict[str, Any]:
    """Import ``competition/round_configs/round<N>.py`` and return ROUND_CONFIG.

    The config file is loaded by absolute path so the orchestrator works
    when invoked as a script (no implicit ``competition`` package on
    sys.path).
    """
    config_path = _COMPETITION_DIR / f"round{round_number}_config.py"
    if not config_path.is_file():
        raise FileNotFoundError(f"Round config not found: {config_path}")

    spec = importlib.util.spec_from_file_location(
        f"_timi_round{round_number}_config",
        config_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot build import spec for {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = getattr(module, "ROUND_CONFIG", None)
    if not isinstance(config, dict):
        raise ValueError(f"{config_path} does not expose a ROUND_CONFIG dict")
    return config


def _pairs_from_config(config: dict[str, Any]) -> list[str]:
    """Extract pair short names (e.g., ``BTCUSDT``) from ROUND_CONFIG.

    The config stores instruments as ``BTCUSDT.BINANCE`` strings; we
    strip the venue suffix to match the per-pair directory naming.
    """
    instruments = config.get("instruments_allowlist") or []
    pairs: list[str] = []
    for inst in instruments:
        s = str(inst)
        pair = s.split(".", 1)[0] if "." in s else s
        pairs.append(pair)
    return pairs


# ---------------------------------------------------------------------------
# Validator + per-pair backtest helpers
# ---------------------------------------------------------------------------


def _run_validator(submission_dir: Path) -> tuple[bool, str]:
    """Invoke the existing validator on ``submission_dir``.

    Returns ``(passed, error_summary)``. Imports the validator in-process
    so we get a structured Report object back rather than parsing CLI
    text. Auto-enforce TiMi laws is left to the validator's own
    auto-detect (the path lives under ``agent-*-timi/`` so step 8 fires).
    """
    from validate_submission import validate  # noqa: PLC0415

    try:
        report = validate(submission_dir)
    except Exception as err:  # pragma: no cover - validator catches its own
        return False, f"validator crashed: {err}"

    if report.has_failures():
        failures = [
            f"step{r.step} {r.title}"
            for r in report.results
            if r.status == "FAIL"
        ]
        return False, "; ".join(failures) or "validation failed"
    return True, ""


def _run_backtest(submission_dir: Path, capital: Decimal) -> tuple[Decimal | None, str]:
    """Run the per-pair evaluator on ``submission_dir``.

    Imports ``_evaluate_pair`` from ``competition.evaluate`` directly (the
    brief explicitly tells us not to subprocess out). Returns
    ``(total_return_pct, error_summary)``. ``total_return_pct`` is
    ``None`` on a non-OK status.
    """
    from evaluate import EvalContext, ResultStatus, _evaluate_pair  # noqa: PLC0415

    ctx = EvalContext(
        round_num=11,
        eval_start="",
        eval_end="",
        initial_capital=capital,
        catalog_path=None,
        allowlist=(),
    )
    result = _evaluate_pair(
        submission_dir,
        ctx,
        capital=capital,
        agent_slug=submission_dir.parent.name + "::" + submission_dir.name,
    )
    if result.status != ResultStatus.OK:
        return None, result.error or result.status
    return Decimal(str(result.total_return_pct)), ""


# ---------------------------------------------------------------------------
# Submission scaffolding
# ---------------------------------------------------------------------------


def _ensure_pair_submission_dir(round_number: int, pair: str) -> Path:
    """Return the per-pair submission directory, creating skeleton if missing.

    The orchestrator only creates the empty directory tree — A_be is the
    only agent allowed to write strategy.py / tests / etc. (per the
    A_be.md prompt). The skeleton ensures the directory exists so
    A_be doesn't have to mkdir during its own writes.
    """
    pair_dir = TIMI_AGENT_ROOT / f"round{round_number}" / pair
    pair_dir.mkdir(parents=True, exist_ok=True)
    return pair_dir


# ---------------------------------------------------------------------------
# Round-report rendering
# ---------------------------------------------------------------------------


def _render_round_report(report: RoundReport, pairs: list[str]) -> str:
    """Render the per-round summary markdown.

    Format follows the brief's ROUND_REPORT.md schema. Pairs that
    converged, diverged, or hit the budget all show up here so the
    operator can quickly see which pairs are healthy.
    """
    lines: list[str] = []
    lines.append(f"# Round {report.round_number} TiMi orchestrator report")
    lines.append("")
    lines.append(f"**Round**: {report.round_number}")
    lines.append(f"**Pairs**: {', '.join(pairs)}")
    lines.append(f"**Outcome**: {report.outcome}")
    lines.append(f"**Total token cost**: ${report.total_token_cost_usd:.2f}")
    lines.append("")
    lines.append("## Per-pair results")
    lines.append("")
    for pair_report in report.per_pair_reports:
        lines.append(f"### {pair_report.pair}")
        lines.append(f"- iterations: {pair_report.iterations}")
        lines.append(f"- final objective: {pair_report.final_objective}%")
        lines.append(f"- outcome: {pair_report.outcome}")
        lines.append(f"- strategy: {pair_report.strategy_path}")
        lines.append("")
        if pair_report.convergence_trace:
            lines.append("#### Convergence trace")
            for step, record in enumerate(pair_report.convergence_trace, start=1):
                if record.agent == "a_be":
                    metric = (
                        f"backtest {record.backtest_metric}%"
                        if record.backtest_metric is not None
                        else "no backtest"
                    )
                    lines.append(f"{step}. a_be: validated → {metric}")
                else:
                    layer = record.directive_layer or "?"
                    verdict = record.verdict or "?"
                    lines.append(f"{step}. a_fr: layer={layer}, verdict={verdict}")
            lines.append("")
    return "\n".join(lines) + "\n"


def _write_round_report(
    round_number: int,
    report: RoundReport,
    pairs: list[str],
) -> Path:
    """Write the round report file. Caller decides whether to call this."""
    round_dir = TIMI_AGENT_ROOT / f"round{round_number}"
    round_dir.mkdir(parents=True, exist_ok=True)
    report_path = round_dir / "ROUND_REPORT.md"
    report_path.write_text(_render_round_report(report, pairs), encoding="utf-8")
    return report_path


# ---------------------------------------------------------------------------
# Per-pair iteration loop
# ---------------------------------------------------------------------------


def _latest_directive_for(round_number: int, pair: str, iteration: int) -> Path:
    """Compute the expected reflection directive path for an iteration."""
    iter_str = f"{iteration:02d}"
    return REFLECTION_DIR / f"round{round_number}__{pair}__{iter_str}.md"


def _user_prompt_for(
    agent_name: str,
    round_number: int,
    pair: str | None,
    iteration: int | None,
    redacted_config: dict[str, Any],
) -> str:
    """Build a one-line user prompt for a given agent.

    The prompt is informational only — the system prompt does the heavy
    lifting. We keep this short so the HiddenWindowGuard's substring
    check has the smallest possible attack surface.
    """
    payload: dict[str, Any] = {
        "agent": agent_name,
        "round": round_number,
        "config": redacted_config,
    }
    if pair is not None:
        payload["pair"] = pair
    if iteration is not None:
        payload["iter"] = iteration
    return json.dumps(payload, default=str, sort_keys=True)


def _run_pair_loop(
    *,
    round_number: int,
    pair: str,
    pair_dir: Path,
    invoker: AgentInvoker,
    guard: HiddenWindowGuard,
    redacted_config: dict[str, Any],
    capital_per_pair: Decimal,
) -> PairReport:
    """Run the A_be ↔ A_fr loop for a single pair until a stop fires.

    Stop conditions (OR-combined, per a_fr.md):

    * Cumulative cost > ``TOKEN_BUDGET_USD_PER_PAIR`` → ``budget_exhausted``.
    * Iterations reach ``MAX_ITERS_PER_PAIR`` → ``max_iters``.
    * Δ objective < ``CONVERGENCE_DELTA_PCT`` for two consecutive iters
      → ``converged`` (also accepts an explicit A_fr ``verdict: converged``).
    * A_fr emits ``verdict: diverged`` → ``diverged``.
    * Validator fails twice in a row → ``validator_failed``.
    * Strategy-layer directive → ``human_review_required`` HOLD.

    The loop produces ``IterationRecord`` entries (one per agent call)
    that downstream renders into the round report's convergence trace.
    """
    a_be_prompt = PROMPTS_DIR / "a_be.md"
    a_fr_prompt = PROMPTS_DIR / "a_fr.md"

    trace: list[IterationRecord] = []
    pair_cost = Decimal("0")
    last_metric: Decimal | None = None
    deltas_below_threshold = 0
    consecutive_validator_failures = 0
    outcome: str = OUTCOME_MAX_ITERS

    for iteration in range(1, MAX_ITERS_PER_PAIR + 1):
        # ----- Step A: A_be writes / refines the bot --------------------
        a_be_user = _user_prompt_for(
            "a_be",
            round_number=round_number,
            pair=pair,
            iteration=iteration,
            redacted_config=redacted_config,
        )
        guard.assert_prompt_clean(a_be_user)
        a_be_result = invoker.invoke("a_be", a_be_prompt, a_be_user)
        a_be_cost = a_be_result.cost_usd
        pair_cost += a_be_cost

        if A_BE_SENTINEL not in a_be_result.stdout:
            trace.append(
                IterationRecord(
                    iter=iteration,
                    agent="a_be",
                    backtest_metric=None,
                    directive_layer=None,
                    verdict=None,
                    token_cost_usd=a_be_cost,
                )
            )
            outcome = OUTCOME_VALIDATOR_FAILED
            logger.warning(
                "A_be did not emit %s for %s iter=%d (cost so far $%s)",
                A_BE_SENTINEL,
                pair,
                iteration,
                pair_cost,
            )
            break

        # ----- Step B: orchestrator validates the submission ------------
        passed, validator_err = _run_validator(pair_dir)
        if not passed:
            consecutive_validator_failures += 1
            trace.append(
                IterationRecord(
                    iter=iteration,
                    agent="a_be",
                    backtest_metric=None,
                    directive_layer=None,
                    verdict=None,
                    token_cost_usd=a_be_cost,
                )
            )
            logger.warning(
                "Validator failed on %s iter=%d: %s",
                pair,
                iteration,
                validator_err,
            )
            if consecutive_validator_failures >= 2:
                outcome = OUTCOME_VALIDATOR_FAILED
                break
            # Budget check between validator retries.
            if pair_cost >= TOKEN_BUDGET_USD_PER_PAIR:
                outcome = OUTCOME_BUDGET_EXHAUSTED
                break
            continue
        consecutive_validator_failures = 0

        # ----- Step C: orchestrator runs the per-pair backtest ----------
        backtest_metric, backtest_err = _run_backtest(pair_dir, capital_per_pair)
        if backtest_err:
            logger.warning(
                "Backtest failed for %s iter=%d: %s",
                pair,
                iteration,
                backtest_err,
            )
        trace.append(
            IterationRecord(
                iter=iteration,
                agent="a_be",
                backtest_metric=backtest_metric,
                directive_layer=None,
                verdict=None,
                token_cost_usd=a_be_cost,
            )
        )

        # ----- Step D: budget check before invoking A_fr ----------------
        if pair_cost >= TOKEN_BUDGET_USD_PER_PAIR:
            outcome = OUTCOME_BUDGET_EXHAUSTED
            break

        # ----- Step E: A_fr emits a refinement directive ----------------
        a_fr_user = _user_prompt_for(
            "a_fr",
            round_number=round_number,
            pair=pair,
            iteration=iteration,
            redacted_config=redacted_config,
        )
        guard.assert_prompt_clean(a_fr_user)
        a_fr_result = invoker.invoke("a_fr", a_fr_prompt, a_fr_user)
        a_fr_cost = a_fr_result.cost_usd
        pair_cost += a_fr_cost

        if A_FR_SENTINEL not in a_fr_result.stdout:
            trace.append(
                IterationRecord(
                    iter=iteration,
                    agent="a_fr",
                    backtest_metric=None,
                    directive_layer=None,
                    verdict=None,
                    token_cost_usd=a_fr_cost,
                )
            )
            outcome = OUTCOME_DIRECTIVE_MALFORMED
            break

        directive_path = _latest_directive_for(round_number, pair, iteration)
        try:
            directive = parse_directive(directive_path)
        except DirectiveSchemaViolation as err:
            logger.error(
                "Directive parse failed for %s iter=%d: %s",
                pair,
                iteration,
                err,
            )
            trace.append(
                IterationRecord(
                    iter=iteration,
                    agent="a_fr",
                    backtest_metric=None,
                    directive_layer=None,
                    verdict=None,
                    token_cost_usd=a_fr_cost,
                )
            )
            outcome = OUTCOME_DIRECTIVE_MALFORMED
            break

        trace.append(
            IterationRecord(
                iter=iteration,
                agent="a_fr",
                backtest_metric=backtest_metric,
                directive_layer=directive.envelope.layer,
                verdict=directive.envelope.verdict,
                token_cost_usd=a_fr_cost,
            )
        )

        # ----- Step F: route on the verdict + layer ---------------------
        if directive.envelope.verdict == "converged":
            outcome = OUTCOME_CONVERGED
            last_metric = backtest_metric if backtest_metric is not None else last_metric
            break
        if directive.envelope.verdict == "diverged":
            outcome = OUTCOME_DIVERGED
            break
        if directive.envelope.layer == "strategy":
            outcome = OUTCOME_HUMAN_HOLD
            break

        # Δ-objective convergence check (A_fr's verdict is the source of
        # truth, but the orchestrator also enforces the rule defensively
        # in case the agent forgets to flip the flag).
        if last_metric is not None and backtest_metric is not None:
            delta = abs(backtest_metric - last_metric)
            if delta < CONVERGENCE_DELTA_PCT:
                deltas_below_threshold += 1
                if deltas_below_threshold >= 2:
                    outcome = OUTCOME_CONVERGED
                    break
            else:
                deltas_below_threshold = 0
        if backtest_metric is not None:
            last_metric = backtest_metric

        # Budget check before next loop iter (catches an A_fr that just
        # blew through cost without verdicts).
        if pair_cost >= TOKEN_BUDGET_USD_PER_PAIR:
            outcome = OUTCOME_BUDGET_EXHAUSTED
            break

    final_objective = last_metric if last_metric is not None else Decimal("0")
    return PairReport(
        pair=pair,
        iterations=iteration,
        final_objective=final_objective,
        convergence_trace=trace,
        strategy_path=pair_dir / "strategy.py",
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_round(
    round_number: int,
    *,
    dry_run: bool = False,
    invoker: AgentInvoker | None = None,
) -> RoundReport:
    """Execute one TiMi evolution round.

    Reads the round config, spawns A_ma once, then loops over each pair
    in the allowlist running A_sa → (A_be ↔ A_fr) until convergence,
    divergence, budget, or max-iter. Returns a ``RoundReport`` summarizing
    every pair's trace and writes ``ROUND_REPORT.md`` under the
    ``competition/agent-6-timi/round<N>/`` directory.

    Set ``dry_run=True`` for the CI plan-only path: returns the same
    ``RoundReport`` shape but without invoking any agent. The pairs
    appear in the report but every trace is empty and ``outcome`` is
    ``"dry_run"``.

    ``invoker`` lets tests inject a ``StubInvoker``. When ``None`` and
    ``dry_run`` is False, a ``ClaudeProcessInvoker`` is used (real
    subprocess); tests must always pass an explicit invoker.
    """
    config = _load_round_config(round_number)
    pairs = _pairs_from_config(config)
    eval_period = config.get("eval_period") or {}
    guard = HiddenWindowGuard(
        eval_start=str(eval_period.get("start", "")),
        eval_end=str(eval_period.get("end", "")),
    )
    redacted_config = guard.redact_round_config(config)
    capital = Decimal(str(config.get("initial_capital_usdt", "0")))
    n_pairs = len(pairs)
    capital_per_pair = (
        (capital / Decimal(n_pairs)).quantize(Decimal("0.01"))
        if n_pairs > 0
        else Decimal("0")
    )

    if dry_run:
        empty_reports = [
            PairReport(
                pair=p,
                iterations=0,
                final_objective=Decimal("0"),
                convergence_trace=[],
                strategy_path=TIMI_AGENT_ROOT / f"round{round_number}" / p / "strategy.py",
                outcome="dry_run",
            )
            for p in pairs
        ]
        return RoundReport(
            round_number=round_number,
            per_pair_reports=empty_reports,
            total_token_cost_usd=Decimal("0"),
            outcome="dry_run",
        )

    if invoker is None:
        invoker = ClaudeProcessInvoker()

    # ----- A_ma: one invocation per round, output shared across pairs ----
    a_ma_prompt = PROMPTS_DIR / "a_ma.md"
    a_ma_user = _user_prompt_for(
        "a_ma",
        round_number=round_number,
        pair=None,
        iteration=None,
        redacted_config=redacted_config,
    )
    guard.assert_prompt_clean(a_ma_user)
    a_ma_result = invoker.invoke("a_ma", a_ma_prompt, a_ma_user)
    total_cost = a_ma_result.cost_usd
    if A_MA_SENTINEL not in a_ma_result.stdout:
        logger.error("A_ma failed to emit %s; aborting round", A_MA_SENTINEL)
        empty = [
            PairReport(
                pair=p,
                iterations=0,
                final_objective=Decimal("0"),
                convergence_trace=[],
                strategy_path=TIMI_AGENT_ROOT / f"round{round_number}" / p / "strategy.py",
                outcome=OUTCOME_DIVERGED,
            )
            for p in pairs
        ]
        return RoundReport(
            round_number=round_number,
            per_pair_reports=empty,
            total_token_cost_usd=total_cost,
            outcome=OUTCOME_DIVERGED,
        )

    # ----- Per-pair: A_sa, then A_be ↔ A_fr until stop -------------------
    pair_reports: list[PairReport] = []
    for pair in pairs:
        pair_dir = _ensure_pair_submission_dir(round_number, pair)

        a_sa_prompt = PROMPTS_DIR / "a_sa.md"
        a_sa_user = _user_prompt_for(
            "a_sa",
            round_number=round_number,
            pair=pair,
            iteration=None,
            redacted_config=redacted_config,
        )
        guard.assert_prompt_clean(a_sa_user)
        a_sa_result = invoker.invoke("a_sa", a_sa_prompt, a_sa_user)
        total_cost += a_sa_result.cost_usd

        if A_SA_SENTINEL not in a_sa_result.stdout:
            logger.error(
                "A_sa failed to emit %s for %s; marking pair diverged",
                A_SA_SENTINEL,
                pair,
            )
            pair_reports.append(
                PairReport(
                    pair=pair,
                    iterations=0,
                    final_objective=Decimal("0"),
                    convergence_trace=[],
                    strategy_path=pair_dir / "strategy.py",
                    outcome=OUTCOME_DIVERGED,
                )
            )
            continue

        report = _run_pair_loop(
            round_number=round_number,
            pair=pair,
            pair_dir=pair_dir,
            invoker=invoker,
            guard=guard,
            redacted_config=redacted_config,
            capital_per_pair=capital_per_pair,
        )
        # Sum every iteration's cost into the round-level total.
        for record in report.convergence_trace:
            total_cost += record.token_cost_usd
        pair_reports.append(report)

    aggregate_outcome = _aggregate_outcome(pair_reports)
    round_report = RoundReport(
        round_number=round_number,
        per_pair_reports=pair_reports,
        total_token_cost_usd=total_cost,
        outcome=aggregate_outcome,
    )
    _write_round_report(round_number, round_report, pairs)
    return round_report


def _aggregate_outcome(pair_reports: list[PairReport]) -> str:
    """Fold per-pair outcomes into a single round-level label.

    Rule of thumb: if every pair converged the round converged; if any
    pair diverged or hit budget the round inherits the worst case
    (diverged > budget_exhausted > max_iters > converged).
    """
    if not pair_reports:
        return OUTCOME_DIVERGED
    outcomes = {p.outcome for p in pair_reports}
    for label in (
        OUTCOME_DIVERGED,
        OUTCOME_BUDGET_EXHAUSTED,
        OUTCOME_VALIDATOR_FAILED,
        OUTCOME_DIRECTIVE_MALFORMED,
        OUTCOME_HUMAN_HOLD,
        OUTCOME_MAX_ITERS,
    ):
        if label in outcomes:
            return label
    return OUTCOME_CONVERGED


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_dry_run_plan(round_number: int, pairs: list[str]) -> None:
    """Render the execution plan for ``--dry-run``.

    Plain text, single-pass, no agents invoked. The plan lists the pairs
    the orchestrator would have processed and the stopping rules — useful
    for CI smoke tests and operator dry-runs.
    """
    print(f"=== TiMi orchestrator dry-run for round {round_number} ===")
    print(f"Pairs ({len(pairs)}): {', '.join(pairs) if pairs else '(none)'}")
    print("Per-round agents:")
    print("  1. A_ma — macro analysis (one invocation)")
    print("Per-pair loop:")
    print("  2. A_sa — strategy adaptation")
    print("  3. A_be ↔ A_fr — refinement loop")
    print("Stopping rules (OR-combined):")
    print(f"  - max iterations per pair: {MAX_ITERS_PER_PAIR}")
    print(f"  - convergence Δ J(π_Θ): < {CONVERGENCE_DELTA_PCT}% for 2 consecutive iters")
    print(f"  - token budget per pair: ${TOKEN_BUDGET_USD_PER_PAIR}")
    print("Hidden eval window: REDACTED at the orchestrator boundary")
    print(f"Submission root: {TIMI_AGENT_ROOT / f'round{round_number}'}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``competition/timi_orchestrator.py``.

    Returns 0 on success, 2 on bad CLI arguments, 1 if the round produced
    no usable submissions.
    """
    parser = argparse.ArgumentParser(
        prog="timi_orchestrator",
        description="Run one TiMi evolution round (A_ma → A_sa → A_be ↔ A_fr).",
    )
    parser.add_argument(
        "--round",
        type=int,
        required=True,
        help="Round number, e.g. 11. Must match a competition/round_configs/round<N>.py",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the execution plan without invoking any agent.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        config = _load_round_config(args.round)
    except (FileNotFoundError, ImportError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2

    pairs = _pairs_from_config(config)
    if args.dry_run:
        _print_dry_run_plan(args.round, pairs)
        return 0

    report = run_round(args.round, dry_run=False)
    print(_render_round_report(report, pairs))
    if not report.per_pair_reports:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - manual CLI entry
    raise SystemExit(main())
