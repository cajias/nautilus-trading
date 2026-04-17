"""
Strategy Validator for Round 11+ Competition.

Validates that a strategy file satisfies the NT paper-trade deployment contract
defined in ROUND11_CONTRACT.md.

Usage:
    cd nautilus && uv run python ../competition/validate_strategy.py <path/to/strategy.py>

Exit codes:
    0  — all checks passed
    1  — one or more checks failed
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root and nautilus src are importable so strategies that
# depend on sibling packages (e.g. strategies.crypto.risk_guard) can load.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
for _p in [str(_PROJECT_ROOT), str(_PROJECT_ROOT / "nautilus" / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Check helpers
# ---------------------------------------------------------------------------

def _pass(msg: str) -> None:
    print(f"  [PASS] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_importable(strategy_path: Path) -> tuple[object | None, list[type], list[type]]:
    """Load the module; return (module, strategy_classes, config_classes)."""
    import hashlib
    # Use a unique module name per file to avoid sys.modules caching between calls
    _hash = hashlib.md5(str(strategy_path).encode()).hexdigest()[:8]
    module_name = f"_submission_{_hash}"

    # Remove any stale cached version
    sys.modules.pop(module_name, None)

    spec = importlib.util.spec_from_file_location(module_name, strategy_path)
    mod = importlib.util.module_from_spec(spec)
    mod.__spec__ = spec
    mod.__file__ = str(strategy_path)
    sys.modules[module_name] = mod

    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        _fail(f"Module failed to import: {e}")
        sys.modules.pop(module_name, None)
        return None, [], []

    _pass("Module imports without errors")
    return mod, [], []


def check_strategy_subclass(mod: object) -> type | None:
    """Find a Strategy subclass in the module."""
    try:
        from nautilus_trader.trading.strategy import Strategy
    except ImportError:
        _warn("nautilus_trader not installed — skipping Strategy subclass check")
        return None

    module_name = getattr(mod, "__name__", "_submission")
    found = []
    for _name, obj in inspect.getmembers(mod, inspect.isclass):
        if (
            issubclass(obj, Strategy)
            and obj is not Strategy
            and obj.__module__ == module_name
        ):
            found.append(obj)

    if not found:
        _fail("No nautilus_trader.trading.strategy.Strategy subclass found")
        return None

    if len(found) > 1:
        _warn(f"Multiple Strategy subclasses found: {[c.__name__ for c in found]}. Using first.")

    cls = found[0]
    _pass(f"Strategy subclass found: {cls.__name__}")
    return cls


def check_config_subclass(mod: object) -> type | None:
    """Find a StrategyConfig subclass in the module."""
    try:
        from nautilus_trader.config import StrategyConfig
    except ImportError:
        _warn("nautilus_trader not installed — skipping StrategyConfig check")
        return None

    module_name = getattr(mod, "__name__", "_submission")
    found = []
    for _name, obj in inspect.getmembers(mod, inspect.isclass):
        if (
            issubclass(obj, StrategyConfig)
            and obj is not StrategyConfig
            and obj.__module__ == module_name
        ):
            found.append(obj)

    if not found:
        _fail("No nautilus_trader.config.StrategyConfig subclass found")
        return None

    cls = found[0]
    _pass(f"StrategyConfig subclass found: {cls.__name__}")
    return cls


def check_config_frozen(config_cls: type) -> bool:
    """Verify config is frozen (immutable)."""
    # msgspec frozen=True sets __struct_config__ or the class is a frozen dataclass
    is_frozen = getattr(config_cls, "__struct_config__", None)
    if is_frozen is not None:
        frozen_val = getattr(is_frozen, "frozen", None)
        if frozen_val:
            _pass("StrategyConfig has frozen=True")
            return True

    # Check via msgspec Struct introspection
    try:
        import msgspec
        if isinstance(config_cls, type) and issubclass(config_cls, msgspec.Struct):
            cfg = config_cls.__struct_config__
            if cfg.frozen:
                _pass("StrategyConfig has frozen=True")
                return True
    except ImportError:
        pass

    _fail("StrategyConfig does not have frozen=True")
    return False


def check_required_config_fields(config_cls: type) -> bool:
    """Check that config has instrument_id and bar_type (universally required).

    trade_size is recommended but not required — some strategies use strategy-specific
    sizing fields (e.g. DCA bots use buy_amount in dollar terms).
    """
    required = {"instrument_id", "bar_type"}
    recommended = {"trade_size"}
    try:
        hints = {}
        for klass in reversed(config_cls.__mro__):
            if hasattr(klass, "__annotations__"):
                hints.update(klass.__annotations__)
        present = set(hints.keys())
    except Exception:
        _warn("Could not inspect config annotations")
        return False

    missing = required - present
    if missing:
        _fail(f"StrategyConfig missing required fields: {sorted(missing)}")
        return False

    _pass(f"StrategyConfig has required fields: {sorted(required)}")

    missing_rec = recommended - present
    if missing_rec:
        _warn(
            f"StrategyConfig missing recommended fields: {sorted(missing_rec)}. "
            "If sizing uses a different field (e.g. buy_amount), this is fine."
        )
    return True


def check_lifecycle_methods(strategy_cls: type) -> bool:
    """Verify on_start, on_bar, on_stop are defined on the strategy."""
    required = {"on_start", "on_bar", "on_stop"}
    missing = []
    for method in required:
        # Must be defined ON this class, not just inherited from Strategy base
        if method not in strategy_cls.__dict__:
            missing.append(method)

    if missing:
        _fail(f"Strategy missing required method overrides: {missing}")
        return False

    _pass(f"Strategy implements required lifecycle methods: {sorted(required)}")
    return True


def check_super_init(strategy_cls: type, strategy_path: Path | None = None) -> bool:
    """Check that __init__ calls super().__init__(config)."""
    init = strategy_cls.__dict__.get("__init__")
    if init is None:
        _warn("Strategy has no __init__ — relies on base class (OK if no extra state)")
        return True

    # Read source directly from file for reliability
    src = None
    if strategy_path and strategy_path.exists():
        src = strategy_path.read_text()
    if src is None:
        try:
            src = inspect.getsource(strategy_cls)
        except (TypeError, OSError):
            _warn("Could not inspect __init__ source — cannot verify super().__init__ call")
            return True

    if "super().__init__" not in src and "Strategy.__init__" not in src:
        _fail("Strategy.__init__ does not call super().__init__(config)")
        return False

    _pass("Strategy.__init__ calls super().__init__(config)")
    return True


def check_on_stop_cleanup(strategy_cls: type, strategy_path: Path | None = None) -> bool:
    """Check that on_stop cancels orders and closes positions."""
    on_stop = strategy_cls.__dict__.get("on_stop")
    if on_stop is None:
        _fail("on_stop() not implemented on strategy class")
        return False

    # Read the source file directly — most reliable approach
    if strategy_path and strategy_path.exists():
        full_text = strategy_path.read_text()
        # Extract the on_stop method body from the file text
        # Simple heuristic: find "def on_stop" and scan until next "def " or end of class
        lines = full_text.splitlines()
        in_on_stop = False
        on_stop_lines = []
        for line in lines:
            if "def on_stop" in line:
                in_on_stop = True
                on_stop_lines.append(line)
                continue
            if in_on_stop:
                # Next method or class ends the on_stop block
                stripped = line.lstrip()
                if stripped.startswith("def ") or (stripped.startswith("class ") and line[0] != " "):
                    break
                on_stop_lines.append(line)
        src = "\n".join(on_stop_lines) if on_stop_lines else full_text
    else:
        try:
            src = inspect.getsource(on_stop)
        except (TypeError, OSError):
            _fail("Could not inspect on_stop() source — cannot verify cleanup calls")
            return False

    has_cancel = "cancel_all_orders" in src
    has_close = "close_all_positions" in src

    if not has_cancel:
        _fail("on_stop() does not call cancel_all_orders()")
    if not has_close:
        _fail("on_stop() does not call close_all_positions()")

    if has_cancel and has_close:
        _pass("on_stop() calls cancel_all_orders() and close_all_positions()")
        return True
    return False


def check_no_hardcoded_api_calls(strategy_path: Path) -> bool:
    """Ensure strategy doesn't make live API calls (requests, urllib)."""
    text = strategy_path.read_text()
    forbidden = ["import requests", "urllib.request", "requests.get", "requests.post"]
    found = [f for f in forbidden if f in text]
    if found:
        _fail(f"Strategy makes external HTTP calls (not allowed in backtest): {found}")
        return False
    _pass("No external HTTP calls found")
    return True


def check_no_pandas_backtest(strategy_path: Path) -> bool:
    """Warn if strategy has a run_backtest() function (pandas-only pattern)."""
    text = strategy_path.read_text()
    if "def run_backtest(" in text:
        _fail(
            "Strategy contains run_backtest() — this is the pandas-only simulation pattern. "
            "Remove it and implement the NautilusTrader Strategy subclass instead."
        )
        return False
    _pass("No run_backtest() function (correct NT pattern)")
    return True


def check_stop_loss_present(strategy_cls: type) -> bool:
    """Heuristic: check that stop-loss logic exists in the strategy."""
    try:
        src = inspect.getsource(strategy_cls)
    except (TypeError, OSError):
        _warn("Could not inspect strategy source for stop-loss check")
        return False
    indicators = [
        "stop_loss",
        "stop_price",
        "StopMarket",
        "stop_market",
        "OrderSide.SELL",  # at minimum sells to exit
        "close_all_positions",
    ]
    found = [kw for kw in indicators if kw in src]
    if not found:
        _warn(
            "Could not find stop-loss indicators in strategy source. "
            "Ensure risk management is implemented."
        )
        return False
    _pass(f"Risk management keywords found: {found[:3]}")
    return True


# ---------------------------------------------------------------------------
# Main validation runner
# ---------------------------------------------------------------------------

def validate(strategy_path: Path) -> bool:
    """Run all checks. Returns True if all mandatory checks pass."""
    print(f"\nValidating: {strategy_path}")
    print("=" * 60)

    # Static checks (no import required)
    print("\n[Static checks]")
    static_ok = True
    static_ok &= check_no_hardcoded_api_calls(strategy_path)
    static_ok &= check_no_pandas_backtest(strategy_path)

    # Import
    print("\n[Import check]")
    mod, _, _ = check_importable(strategy_path)
    if mod is None:
        print("\nResult: FAIL — module could not be imported")
        return False

    # Class structure
    print("\n[Structure checks]")
    strategy_cls = check_strategy_subclass(mod)
    config_cls = check_config_subclass(mod)

    structure_ok = strategy_cls is not None and config_cls is not None

    config_ok = True
    if config_cls is not None:
        config_ok &= check_config_frozen(config_cls)
        config_ok &= check_required_config_fields(config_cls)

    strategy_ok = True
    if strategy_cls is not None:
        strategy_ok &= check_lifecycle_methods(strategy_cls)
        strategy_ok &= check_super_init(strategy_cls, strategy_path)
        strategy_ok &= check_on_stop_cleanup(strategy_cls, strategy_path)
        check_stop_loss_present(strategy_cls)  # warning only

    all_ok = static_ok and structure_ok and config_ok and strategy_ok

    print("\n" + "=" * 60)
    if all_ok:
        print("Result: PASS — strategy satisfies the Round 11 contract")
    else:
        print("Result: FAIL — fix the issues above before submitting")
    print("=" * 60)
    return all_ok


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python validate_strategy.py <path/to/strategy.py>")
        print("\nValidates a strategy against the Round 11 competition contract.")
        print("See competition/ROUND11_CONTRACT.md for requirements.")
        sys.exit(1)

    strategy_path = Path(sys.argv[1])
    if not strategy_path.exists():
        print(f"Error: {strategy_path} not found")
        sys.exit(1)

    ok = validate(strategy_path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
