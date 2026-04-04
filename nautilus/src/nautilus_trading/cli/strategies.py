"""CLI command for discovering available trading strategies."""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table


def _get_project_root() -> Path:
    """Return the project root (parent of the ``nautilus/`` package dir)."""
    return Path(__file__).resolve().parents[4]


def _ensure_project_root_on_path() -> None:
    """Add the project root to sys.path so strategy modules are importable."""
    project_root = str(_get_project_root())
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def _find_market_dirs(strategies_dir: Path) -> list[Path]:
    """Return subdirectories of strategies/ that contain an __init__.py."""
    if not strategies_dir.is_dir():
        return []
    return sorted(
        d
        for d in strategies_dir.iterdir()
        if d.is_dir() and (d / "__init__.py").exists()
    )


def _scan_strategy_files(market_dir: Path) -> list[Path]:
    """Return .py files in a market directory, excluding __init__.py."""
    return sorted(
        f
        for f in market_dir.glob("*.py")
        if f.name != "__init__.py"
    )


def _find_strategy_classes(
    module_import_path: str,
) -> tuple[list[tuple[str, str]], str | None]:
    """Try to import a module and find Strategy / StrategyConfig subclasses.

    Returns a list of (strategy_name, config_name) pairs and an optional error message.
    """
    try:
        from nautilus_trader.config import StrategyConfig
        from nautilus_trader.trading.strategy import Strategy
    except ImportError:
        return [], "nautilus_trader not installed"

    try:
        mod = importlib.import_module(module_import_path)
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)

    strategies: list[str] = []
    configs: list[str] = []

    for name, obj in inspect.getmembers(mod, inspect.isclass):
        if obj.__module__ != mod.__name__:
            continue
        if issubclass(obj, Strategy) and obj is not Strategy:
            strategies.append(name)
        if issubclass(obj, StrategyConfig) and obj is not StrategyConfig:
            configs.append(name)

    pairs: list[tuple[str, str]] = []
    for strat_name in strategies:
        # Heuristic: match config by name prefix (e.g. EMACrossStrategy -> EMACrossConfig)
        matched_config = ""
        base = strat_name.replace("Strategy", "")
        for cfg in configs:
            if cfg.startswith(base):
                matched_config = cfg
                break
        if not matched_config and len(configs) == 1:
            matched_config = configs[0]
        pairs.append((strat_name, matched_config))

    if not pairs:
        # No strategy classes found but also no import error
        return [], None

    return pairs, None


def strategies() -> None:
    """Discover and list all available trading strategies."""
    _ensure_project_root_on_path()

    project_root = _get_project_root()
    strategies_dir = project_root / "strategies"

    console = Console()

    if not strategies_dir.is_dir():
        console.print(
            f"[red]strategies/ directory not found at {strategies_dir}[/red]"
        )
        raise SystemExit(1)

    table = Table(title="Available Strategies")
    table.add_column("Market", style="cyan")
    table.add_column("Strategy", style="green")
    table.add_column("Config", style="yellow")
    table.add_column("Notebook", style="magenta", justify="center")
    table.add_column("Import Path", style="dim")

    market_dirs = _find_market_dirs(strategies_dir)

    for market_dir in market_dirs:
        market_name = market_dir.name
        py_files = _scan_strategy_files(market_dir)

        if not py_files:
            table.add_row(market_name, "(none yet)", "", "", "")
            continue

        first_row_for_market = True

        for py_file in py_files:
            module_name = py_file.stem
            import_path = f"strategies.{market_name}.{module_name}"

            # Check for co-located notebook
            notebook = py_file.with_suffix(".ipynb")
            # Also check for _backtest variant
            notebook_backtest = py_file.with_name(f"{module_name}_backtest.ipynb")
            has_notebook = notebook.exists() or notebook_backtest.exists()
            notebook_icon = "\u2713" if has_notebook else ""

            pairs, error = _find_strategy_classes(import_path)

            display_market = market_name if first_row_for_market else ""

            if error:
                table.add_row(
                    display_market,
                    f"{module_name} [red](import error)[/red]",
                    "",
                    notebook_icon,
                    import_path,
                )
                first_row_for_market = False
            elif not pairs:
                table.add_row(
                    display_market,
                    f"{module_name} [dim](no Strategy classes)[/dim]",
                    "",
                    notebook_icon,
                    import_path,
                )
                first_row_for_market = False
            else:
                for strat_name, config_name in pairs:
                    table.add_row(
                        display_market,
                        strat_name,
                        config_name,
                        notebook_icon,
                        import_path,
                    )
                    display_market = ""
                    first_row_for_market = False

    console.print(table)
