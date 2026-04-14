"""AST-level enforcement of TiMi's three programming laws.

Quoted verbatim from the TiMi paper (arxiv 2510.04787, Section 2.4):

    1. Functional cohesion law -- "each functional component must address
       exactly one responsibility".
    2. Unidirectional dependency law -- "dependencies flow strictly from
       higher to lower layers".
    3. Parameter externalization law -- "all adjustable values must be
       extracted from implementation code and centrally managed".

These checks are opt-in: ``validate_submission.py`` only runs them when the
``--enforce-timi-laws`` flag is passed or when the submission path contains
``agent-*-timi``. They must NOT run by default on the template or on any
pre-TiMi submissions (agent-1-quant ... agent-5-hybrid), which do not claim
to obey these laws.

Design notes:

- Law 1 uses a *proxy* for cohesion: functions with cyclomatic complexity
  > 10 OR more than 40 AST nodes in their body are flagged, with an
  exemption list for NautilusTrader lifecycle methods.
- Law 2 walks imports across every ``.py`` file in the submission dir and
  rejects any edge from a helper module back to ``strategy``.
- Law 3 is scoped to *method bodies of Strategy subclasses only*. Top-level
  constants, config class bodies, helper functions, and type annotations are
  intentionally out of scope -- this law only bites the strategy layer.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# Cyclomatic complexity threshold above which a function is presumed to be
# doing more than one thing. Matches the common McCabe guidance of <= 10.
_COMPLEXITY_THRESHOLD = 10

# AST body-node threshold. A 40-node function body is already "large" in the
# sense the cohesion law cares about; trading strategies routinely exceed
# this only inside the exempted lifecycle methods.
_BODY_SIZE_THRESHOLD = 40

# Lifecycle methods exempt from the cohesion check. These methods
# legitimately orchestrate multiple sub-responsibilities in a single place,
# per the NautilusTrader Strategy contract.
_LIFECYCLE_EXEMPTIONS: frozenset[str] = frozenset(
    {
        "on_bar",
        "on_start",
        "on_stop",
        "on_reset",
        "on_event",
        "on_quote_tick",
        "on_trade_tick",
    }
)

# Numeric literals allowed anywhere inside a strategy method body without
# triggering the externalization law. These are universal coding idioms
# (initial counters, slice sentinels, sign flips) rather than tunable knobs.
_ALLOWED_NUMERIC_LITERALS: frozenset[int | float] = frozenset({0, 1, -1, 2})


@dataclass(frozen=True)
class LawViolation:
    """A single violation of one of the three TiMi programming laws."""

    law: str  # "cohesion" | "dependency" | "externalization"
    file: Path
    line: int
    col: int
    message: str

    def render(self) -> str:
        """Return a human-readable one-line error message."""
        return (
            f"[{self.law}] {self.file}:{self.line}:{self.col}: {self.message}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_strategy_subclass(node: ast.ClassDef) -> bool:
    """Return True if ``node`` has a base class named ``Strategy``.

    Uses name-based detection (``Strategy`` or ``*.Strategy``) to avoid
    importing the actual class at validation time.
    """
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "Strategy":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Strategy":
            return True
    return False


def _cyclomatic_complexity(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Proxy cyclomatic complexity for ``func``.

    Counts control-flow branches (``If``, ``For``, ``While``, ``Try``,
    ``ExceptHandler``, ``IfExp``) plus boolean operators (``and``/``or``)
    and comprehensions that include ``if`` clauses. Starts at 1 for the
    happy path.
    """
    complexity = 1
    for node in ast.walk(func):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.IfExp)):
            complexity += 1
        elif isinstance(node, ast.ExceptHandler):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            # Each additional operand adds a branch.
            complexity += max(0, len(node.values) - 1)
        elif isinstance(
            node,
            (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
        ):
            for gen in node.generators:
                complexity += len(gen.ifs)
    return complexity


def _body_node_count(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count AST nodes inside the function body (excluding the def itself)."""
    return sum(1 for stmt in func.body for _ in ast.walk(stmt))


def _module_name(file_path: Path) -> str:
    """Return the bare module name (no ``.py``) for a file."""
    return file_path.stem


# ---------------------------------------------------------------------------
# Law 1: functional cohesion
# ---------------------------------------------------------------------------


def check_functional_cohesion(tree: ast.AST, path: Path) -> list[LawViolation]:
    """Flag functions that almost certainly violate the cohesion law.

    Two complementary proxies are used together:

    - Cyclomatic complexity > 10 (McCabe).
    - Body AST node count > 40.

    Lifecycle methods of ``Strategy`` subclasses (``on_bar``, ``on_start``,
    etc.) are exempt because they legitimately orchestrate several
    sub-responsibilities.
    """
    violations: list[LawViolation] = []

    # Collect the set of lifecycle method ids within Strategy subclasses.
    # Any other method, top-level function, or nested function is subject to
    # the cohesion proxy unconditionally.
    exempt_functions: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_strategy_subclass(node):
            continue
        for member in node.body:
            if (
                isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                and member.name in _LIFECYCLE_EXEMPTIONS
            ):
                exempt_functions.add(id(member))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if id(node) in exempt_functions:
            continue

        complexity = _cyclomatic_complexity(node)
        body_size = _body_node_count(node)

        if complexity > _COMPLEXITY_THRESHOLD or body_size > _BODY_SIZE_THRESHOLD:
            violations.append(
                LawViolation(
                    law="cohesion",
                    file=path,
                    line=node.lineno,
                    col=node.col_offset,
                    message=(
                        f"function {node.name!r} violates cohesion "
                        f"(cyclomatic={complexity}, body_nodes={body_size}); "
                        f"split into single-responsibility helpers"
                    ),
                )
            )

    return violations


# ---------------------------------------------------------------------------
# Law 2: unidirectional dependency
# ---------------------------------------------------------------------------


def _collect_imports(tree: ast.AST) -> list[tuple[str, int, int]]:
    """Return a list of ``(module, lineno, col_offset)`` for every import."""
    imports: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno, node.col_offset))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append((module, node.lineno, node.col_offset))
    return imports


def check_unidirectional_dependency(submission_dir: Path) -> list[LawViolation]:
    """Reject helper modules that import back from ``strategy``.

    The rule in the paper is that dependencies flow strictly from higher
    layers (strategy) to lower layers (function, parameter). For our
    single-file-per-pair layout, the operational check is:

        helper modules (anything other than ``strategy.py``) must NEVER
        import from ``strategy``.

    The check is *directional*: ``strategy`` importing ``_helpers`` is fine,
    but ``_helpers`` importing ``strategy`` is a violation -- that would be
    a circular back-reference and almost always pulls the helper back into
    the strategy layer's concerns.
    """
    violations: list[LawViolation] = []

    py_files = sorted(submission_dir.glob("*.py"))
    # Also look one level down for a ``helpers/`` package, per the TiMi spec.
    py_files.extend(sorted(submission_dir.glob("helpers/*.py")))

    strategy_file = submission_dir / "strategy.py"
    if not strategy_file.is_file():
        return violations

    for file_path in py_files:
        if file_path.name == "strategy.py":
            continue
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (OSError, SyntaxError):
            continue

        for module, lineno, col in _collect_imports(tree):
            if module == "strategy" or module.endswith(".strategy"):
                violations.append(
                    LawViolation(
                        law="dependency",
                        file=file_path,
                        line=lineno,
                        col=col,
                        message=(
                            f"helper module {file_path.name!r} imports from "
                            f"{module!r}; unidirectional law forbids "
                            f"function-layer modules from importing the "
                            f"strategy layer"
                        ),
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# Law 3: parameter externalization
# ---------------------------------------------------------------------------


def _is_docstring(stmt: ast.stmt) -> bool:
    """Return True if ``stmt`` is a bare string expression (a docstring)."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _looks_like_numeric_string(s: str) -> bool:
    """Return True if ``s`` parses as a float (``"0.5"``, ``"-1.25"``, ...).

    Used by the law-3 check to catch ``Decimal("0.5")`` -- the string is a
    literal that carries a hard-coded tunable. Non-numeric strings like
    ``"BTCUSDT"`` are not magic constants and must not be flagged.
    """
    try:
        float(s)
    except (TypeError, ValueError):
        return False
    return True


def _is_config_access(node: ast.AST) -> bool:
    """Return True if ``node`` is (transitively) a ``self.config.<field>`` read.

    Used to recognise that an expression like ``self.config.sma_fast - 1``
    is reading a tunable field, not hard-coding a value.
    """
    while isinstance(node, ast.Attribute):
        if (
            isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == "config"
        ):
            return True
        node = node.value
    return False


def _traces_to_runtime(arg: ast.expr) -> bool:
    """Return True if ``arg`` reads from runtime data rather than a literal.

    Examples that return True: ``bar.close``, ``str(bar.close)``, ``tick.bid``.
    Examples that return False: ``Decimal("0.5")``, ``Decimal(0.5)``,
    ``"0.5"``.

    This is the subtlety of law 3 -- ``Decimal(str(bar.close))`` must pass
    because the quantity traces to a live bar, but ``Decimal("0.5")`` must
    fail because ``"0.5"`` is a hard-coded magic number.
    """
    if isinstance(arg, ast.Constant):
        # String, int, float, or bool literal -- definitely not runtime.
        return False
    if isinstance(arg, ast.Name):
        # Bare name -- could be a loop var or a runtime binding. Assume yes.
        return True
    if isinstance(arg, ast.Attribute):
        # e.g. ``bar.close``, ``self.config.foo``.
        return True
    if isinstance(arg, ast.Subscript):
        return True
    if isinstance(arg, ast.Call):
        # Recurse into wrapping calls like ``str(bar.close)``.
        for inner in arg.args:
            if _traces_to_runtime(inner):
                return True
        return False
    if isinstance(arg, ast.BinOp):
        return _traces_to_runtime(arg.left) or _traces_to_runtime(arg.right)
    return False


class _ExternalizationVisitor(ast.NodeVisitor):
    """Walk a Strategy method body and collect magic-number violations.

    Instantiated per method. Skips:
    - The docstring (first statement if it's a bare string).
    - Type annotations on parameters, ``AnnAssign`` targets.
    - Numeric literals inside ``self.config.*`` attribute access.
    - Numeric literals in the allowed set ``{0, 1, -1, 2}``.
    - ``Decimal(<runtime expr>)`` calls whose argument traces to non-literal
      data (e.g. ``bar.close``).
    """

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.violations: list[LawViolation] = []
        self._skip_nodes: set[int] = set()

    # -- skip helpers --------------------------------------------------

    def _mark_subtree_skipped(self, node: ast.AST) -> None:
        for sub in ast.walk(node):
            self._skip_nodes.add(id(sub))

    # -- visitors ------------------------------------------------------

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # Skip the annotation subtree -- it's a type, not a value.
        if node.annotation is not None:
            self._mark_subtree_skipped(node.annotation)
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        if node.annotation is not None:
            self._mark_subtree_skipped(node.annotation)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Annotations on arguments and return value are types, not values.
        if node.returns is not None:
            self._mark_subtree_skipped(node.returns)
        for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
            if arg.annotation is not None:
                self._mark_subtree_skipped(arg.annotation)
        if node.args.vararg and node.args.vararg.annotation is not None:
            self._mark_subtree_skipped(node.args.vararg.annotation)
        if node.args.kwarg and node.args.kwarg.annotation is not None:
            self._mark_subtree_skipped(node.args.kwarg.annotation)
        # Skip the docstring, if any.
        if node.body and _is_docstring(node.body[0]):
            self._mark_subtree_skipped(node.body[0])
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if _is_config_access(node):
            self._mark_subtree_skipped(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # ``Decimal("0.5")`` hides a magic number inside a string literal,
        # so we have to detect the pattern directly: it won't be caught by
        # the int/float literal scan in visit_Constant. ``Decimal(str(x))``
        # with a runtime ``x`` is the legitimate escape hatch.
        func = node.func
        is_decimal = (
            (isinstance(func, ast.Name) and func.id == "Decimal")
            or (isinstance(func, ast.Attribute) and func.attr == "Decimal")
        )
        if is_decimal and node.args:
            first = node.args[0]
            if _traces_to_runtime(first):
                self._mark_subtree_skipped(node)
            elif (
                isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and _looks_like_numeric_string(first.value)
            ):
                self.violations.append(
                    LawViolation(
                        law="externalization",
                        file=self.file_path,
                        line=node.lineno,
                        col=node.col_offset,
                        message=(
                            f"magic Decimal({first.value!r}) inside a "
                            f"Strategy method body; move the value to "
                            f"TimiConfig as a Decimal field"
                        ),
                    )
                )
                # Already reported; no need to recurse into it.
                self._mark_subtree_skipped(node)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if id(node) in self._skip_nodes:
            return
        value = node.value
        # Only int/float literals are in scope. bool is a subclass of int --
        # rule it out explicitly so ``True``/``False`` don't get flagged.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        if value in _ALLOWED_NUMERIC_LITERALS:
            return

        self.violations.append(
            LawViolation(
                law="externalization",
                file=self.file_path,
                line=node.lineno,
                col=node.col_offset,
                message=(
                    f"magic numeric literal {value!r} inside a Strategy "
                    f"method body; move it to TimiConfig as a typed field"
                ),
            )
        )


def check_parameter_externalization(
    tree: ast.AST,
    path: Path,
) -> list[LawViolation]:
    """Reject magic numeric literals inside ``Strategy`` subclass methods.

    Scoping is deliberate: top-level module constants, config-class body
    defaults, and helper-function bodies are all out of scope. The law only
    applies to method bodies of ``Strategy`` subclasses, which is where a
    magic constant could actually hide a tunable from the A_fr feedback
    loop.
    """
    violations: list[LawViolation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_strategy_subclass(node):
            continue
        for member in node.body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            visitor = _ExternalizationVisitor(path)
            visitor.visit(member)
            violations.extend(visitor.violations)

    return violations


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


def enforce_laws(submission_dir: Path) -> list[LawViolation]:
    """Run all three laws on ``submission_dir``. Return every violation found.

    Intended for use from ``validate_submission.py``. If ``strategy.py`` is
    missing or unparseable, any law that needs the AST returns ``[]`` for
    that file -- the upstream validator already reports those errors.
    """
    violations: list[LawViolation] = []

    strategy_path = submission_dir / "strategy.py"
    if strategy_path.is_file():
        try:
            source = strategy_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(strategy_path))
        except (OSError, SyntaxError):
            tree = None
        if tree is not None:
            violations.extend(check_functional_cohesion(tree, strategy_path))
            violations.extend(check_parameter_externalization(tree, strategy_path))

    violations.extend(check_unidirectional_dependency(submission_dir))

    return violations
