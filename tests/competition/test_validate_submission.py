"""Tests for ``competition/validate_submission.py``.

Every negative test copies ``competition/TEMPLATE`` to a temp directory,
mutates one aspect, and runs the validator as a subprocess. We assert it
exits non-zero and that stdout contains the expected ``[FAIL]`` marker for
the scenario being tested.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_DIR = _REPO_ROOT / "competition" / "TEMPLATE"
_VALIDATOR = _REPO_ROOT / "competition" / "validate_submission.py"


def _run_validator(submission_dir: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the validator CLI on ``submission_dir``."""
    return subprocess.run(
        [sys.executable, str(_VALIDATOR), str(submission_dir)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.fixture
def template_copy(tmp_path: Path) -> Path:
    """Copy ``competition/TEMPLATE`` to a tmp directory for safe mutation."""
    dest = tmp_path / "submission"
    shutil.copytree(_TEMPLATE_DIR, dest)
    # pytest bytecode cache from any prior run would pin old sources;
    # scrub it before every test.
    for cache in dest.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    return dest


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_template_validates_cleanly(template_copy: Path) -> None:
    """The unmodified TEMPLATE fixture must pass all checks."""
    result = _run_validator(template_copy)
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "PASS: Submission is pluggable." in result.stdout
    assert "[FAIL]" not in result.stdout


# ---------------------------------------------------------------------------
# Step 1: directory structure
# ---------------------------------------------------------------------------


def test_missing_strategy_file(template_copy: Path) -> None:
    """Removing strategy.py must fail at step 1."""
    (template_copy / "strategy.py").unlink()

    result = _run_validator(template_copy)
    assert result.returncode == 1
    assert "[FAIL] 1. Directory structure" in result.stdout
    assert "strategy.py" in result.stdout


# ---------------------------------------------------------------------------
# Step 2: manifest
# ---------------------------------------------------------------------------


def test_missing_manifest(template_copy: Path) -> None:
    """Removing the MANIFEST declaration must fail at step 2."""
    strategy_path = template_copy / "strategy.py"
    source = strategy_path.read_text()
    # Keep imports and classes, drop the MANIFEST dict literal entirely.
    marker = "MANIFEST: dict[str, Any] = {"
    idx = source.index(marker)
    strategy_path.write_text(source[:idx].rstrip() + "\n")

    result = _run_validator(template_copy)
    assert result.returncode == 1
    assert "[FAIL] 2. Manifest has 6 required keys" in result.stdout


def test_manifest_missing_keys(template_copy: Path) -> None:
    """Dropping 'description' from MANIFEST must fail at step 2."""
    strategy_path = template_copy / "strategy.py"
    source = strategy_path.read_text()
    # Remove the line that defines the 'description' key. The template
    # spreads the description across multiple lines, so match a block.
    start = source.index('    "description"')
    # The description value spans until the closing ')' + ',\n'
    end = source.index("),\n", start) + len("),\n")
    mutated = source[:start] + source[end:]
    strategy_path.write_text(mutated)

    result = _run_validator(template_copy)
    assert result.returncode == 1
    assert "[FAIL] 2. Manifest has 6 required keys" in result.stdout
    assert "description" in result.stdout


# ---------------------------------------------------------------------------
# Step 3: class identity
# ---------------------------------------------------------------------------


def test_strategy_not_subclass(template_copy: Path) -> None:
    """Replacing the Strategy subclass with a bare class must fail at step 3."""
    strategy_path = template_copy / "strategy.py"
    source = strategy_path.read_text()
    mutated = source.replace(
        "class TemplateStrategy(Strategy):",
        "class TemplateStrategy:",
    )
    # Drop the super().__init__ call so the bare class doesn't fail to import.
    mutated = mutated.replace("super().__init__(config)", "self.config = config")
    strategy_path.write_text(mutated)

    result = _run_validator(template_copy)
    assert result.returncode == 1
    assert "[FAIL] 3. Strategy class is a Strategy subclass" in result.stdout


# ---------------------------------------------------------------------------
# Step 4: frozen config
# ---------------------------------------------------------------------------


def test_config_not_frozen(template_copy: Path) -> None:
    """A StrategyConfig subclass with frozen=False must fail at step 4."""
    strategy_path = template_copy / "strategy.py"
    source = strategy_path.read_text()
    mutated = source.replace(
        "class TemplateConfig(StrategyConfig, frozen=True):",
        "class TemplateConfig(StrategyConfig, frozen=False):",
    )
    strategy_path.write_text(mutated)

    result = _run_validator(template_copy)
    assert result.returncode == 1
    assert "[FAIL] 4. Config class is frozen" in result.stdout


# ---------------------------------------------------------------------------
# Step 5: static checks
# ---------------------------------------------------------------------------


def test_contains_leverage_keyword(template_copy: Path) -> None:
    """Adding a top-level ``leverage`` identifier must fail at step 5."""
    strategy_path = template_copy / "strategy.py"
    source = strategy_path.read_text()
    # Insert a module-level constant just above MANIFEST.
    mutated = source.replace(
        "# Module-level manifest",
        "leverage = 2.0\n\n# Module-level manifest",
    )
    strategy_path.write_text(mutated)

    result = _run_validator(template_copy)
    assert result.returncode == 1
    assert "[FAIL] 5. No prohibited identifiers" in result.stdout
    assert "leverage" in result.stdout


def test_contains_print_call(template_copy: Path) -> None:
    """Adding a print() call must fail at step 5."""
    strategy_path = template_copy / "strategy.py"
    source = strategy_path.read_text()
    mutated = source.replace(
        'self.log.info("Template strategy started")',
        'print("starting template strategy")\n        self.log.info("Template strategy started")',
    )
    strategy_path.write_text(mutated)

    result = _run_validator(template_copy)
    assert result.returncode == 1
    assert "[FAIL] 5. No print() calls" in result.stdout


def test_uses_round_on_price(template_copy: Path) -> None:
    """round() on a price-named variable must fail at step 5."""
    strategy_path = template_copy / "strategy.py"
    source = strategy_path.read_text()
    insertion = (
        "        raw_price = 1.234567\n"
        "        price = round(raw_price, 2)\n"
        "        self.log.info(f\"price={price}\")\n"
    )
    mutated = source.replace(
        '        self.log.info(f"Bar: {bar}")\n',
        '        self.log.info(f"Bar: {bar}")\n' + insertion,
    )
    strategy_path.write_text(mutated)

    result = _run_validator(template_copy)
    assert result.returncode == 1
    assert "[FAIL] 5. No round() on price variables" in result.stdout


# ---------------------------------------------------------------------------
# Step 6: tests must pass
# ---------------------------------------------------------------------------


def test_tests_failing(template_copy: Path) -> None:
    """Deliberately breaking the test file must fail at step 6."""
    test_path = template_copy / "tests" / "test_strategy.py"
    source = test_path.read_text()
    # Inject a guaranteed failure as a new test function.
    failing_test = "\n\ndef test_forced_failure() -> None:\n    assert False, 'forced'\n"
    test_path.write_text(source + failing_test)

    result = _run_validator(template_copy)
    assert result.returncode == 1
    assert "[FAIL] 6. Tests pass" in result.stdout
