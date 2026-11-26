# SPDX-License-Identifier: MIT
"""The handler subset (docs/handler-subset.md), checked on the syntax tree like rung 0.

Every instruction handler (each decode-table form's ``_op_*`` method, and ``_illegal``
for undefined code) and every helper they reach is parsed with :mod:`ast`, never run,
and rejected if it steps outside the subset: a construct not on the list, a name that
is not a parameter, a local, an integer constant of its module or a pure helper, an
attribute of ``self`` that is not named state or a named method, a call that is not a
bus method, the exception-entry primitive or a listed helper. Then the effects each
handler may have are checked against its class (only ``BT``/``BF`` write the PC, only
the delayed branches and ``RTE`` write ``delay_target``, ...), and its data accesses
against the MA slots of its pipeline class.

The lint proves it can fail: ``test_the_lint_catches_*`` feed it one seeded fault each.

``python tests/test_handler_subset.py`` prints every handler's footprint (state read,
state written, bus accesses) as one line each.
"""

import ast
import sys
from pathlib import Path

import pytest

from sh2_python._decode import FORMS
from sh2_python.pipeline import PLANS

SRC = Path(__file__).resolve().parents[1] / "src" / "sh2_python"

#: The modules whose classes hold the handlers, and _core.py for ``_illegal``.
MODULES = ("_arith.py", "_branch.py", "_logic.py", "_multiply.py", "_shift.py",
           "_system.py", "_transfer.py", "_core.py")  # fmt: skip

#: The architectural state a handler may read (HM 2.1; ``pc`` is the handler's address + 2).
READABLE = frozenset({"R", "sr", "gbr", "vbr", "mach", "macl", "pr", "pc"})
#: ... and write. ``R`` only by subscript; ``delay_target`` and ``sleeping`` write only.
WRITABLE = frozenset({"R", "sr", "gbr", "vbr", "mach", "macl", "pr", "pc", "delay_target",
                      "sleeping"})  # fmt: skip
#: The bus methods (``_core.py``), with the MAs each takes.
BUS = {"_read8": 1, "_read16": 1, "_read32": 1, "_write8": 1, "_write16": 1, "_write32": 1,
       "_tas8": 2}  # fmt: skip
#: Exception entry (``_core.py``): push SR, push PC, read the vector, jump: three MAs.
CONTROL = {"_enter": 3}
#: Methods the handlers share, linted with the same rules as the handlers.
HELPERS = frozenset({"_set_t", "_set_mac", "_push", "_pop"})
BUILTINS = frozenset({"int", "bool"})

#: Who may write the control-flow state (the rest of the subset may not).
EFFECT_OWNERS = {
    "pc": {"_op_bt", "_op_bf"},
    "delay_target": {"_op_bf_s", "_op_bt_s", "_op_bra", "_op_braf", "_op_bsr", "_op_bsrf",
                     "_op_jmp", "_op_jsr", "_op_rts", "_op_rte"},
    "sleeping": {"_op_sleep"},
    "_enter": {"_op_trapa", "_illegal"},
}  # fmt: skip

ALLOWED_NODES = (
    ast.FunctionDef, ast.arguments, ast.arg, ast.Expr, ast.Assign, ast.AugAssign, ast.If,
    ast.Return, ast.Name, ast.Attribute, ast.Subscript, ast.Constant, ast.Tuple, ast.Call,
    ast.keyword, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Load,
    ast.Store,
    # operators: integer arithmetic, masks and shifts; comparisons; logic
    ast.Add, ast.Sub, ast.Mult, ast.BitAnd, ast.BitOr, ast.BitXor, ast.LShift, ast.RShift,
    ast.USub, ast.Invert, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In,
)  # fmt: skip


class Module:
    """One source file: its integer constants, pure helpers and methods."""

    def __init__(self, path: Path, tree: ast.Module) -> None:
        self.path = path
        self.constants: set[str] = set()
        self.functions: dict[str, ast.FunctionDef] = {}
        self.methods: dict[str, ast.FunctionDef] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and _is_int_expression(node.value):
                for target in node.targets:  # ``T = 1`` and ``T, Q, M = 1, 0x100, 0x200``
                    self.constants.update(t.id for t in ast.walk(target) if isinstance(t, ast.Name))
            elif isinstance(node, ast.FunctionDef):
                self.functions[node.name] = node
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        self.methods[item.name] = item


def _is_int_expression(node: ast.expr) -> bool:
    return all(
        isinstance(n, ast.BinOp | ast.UnaryOp | ast.Tuple | ast.Load | ast.operator | ast.unaryop)
        or (isinstance(n, ast.Constant) and type(n.value) is int)
        for n in ast.walk(node)
    )


def _load_modules() -> dict[str, Module]:
    return {
        name: Module(SRC / name, ast.parse((SRC / name).read_text(), filename=name))
        for name in MODULES
    }


MODULE_FILES = _load_modules()


def _find(name: str) -> tuple[Module, ast.FunctionDef]:
    for module in MODULE_FILES.values():
        if name in module.methods:
            return module, module.methods[name]
    raise KeyError(name)


class Footprint:
    def __init__(self) -> None:
        self.reads: set[str] = set()
        self.writes: set[str] = set()
        self.calls: set[str] = set()


def check_function(
    fn: ast.FunctionDef, module: Module, *, method: bool, handler: bool
) -> tuple[list[str], Footprint]:
    """The subset's violations in one function, and what it touches.

    ``method``: a handler or a shared helper (``self`` is its first parameter);
    otherwise a pure module-level helper, which may not touch ``self`` at all.
    ``handler``: exactly ``(self, op)``, no return value.
    """
    where = f"{module.path.name}:{fn.name}"
    errors: list[str] = []
    fp = Footprint()

    def bad(node: ast.AST, why: str) -> None:
        errors.append(f"{where} line {getattr(node, 'lineno', fn.lineno)}: {why}")

    args = fn.args
    params = [a.arg for a in args.args]
    if args.vararg or args.kwarg or args.kwonlyargs or args.posonlyargs:
        bad(fn, "only plain positional parameters")
    if fn.decorator_list:
        bad(fn, "no decorators")
    if method and (not params or params[0] != "self"):
        bad(fn, "a method's first parameter is self")
    if handler and params != ["self", "op"]:
        bad(fn, f"a handler takes (self, op), not {params}")

    # Locals: every name the body binds (assignment targets, tuple targets).
    local = set(params)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            local.add(node.id)
    aliases = {  # names bound to self.R (``R = self.R``)
        t.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
        and node.value.attr == "R"
        for t in node.targets
        if isinstance(t, ast.Name)
    }

    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(fn):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        docstring = body[0].value
    else:
        docstring = None

    # Type annotations are documentation, not code: they are not linted.
    annotations = {
        id(n)
        for a in [fn.returns, *(arg.annotation for arg in args.args)]
        if a is not None
        for n in ast.walk(a)
    }

    for node in ast.walk(fn):
        if id(node) in annotations:
            continue
        if node is not fn and isinstance(node, ast.FunctionDef):
            bad(node, "no nested functions")
            continue
        if not isinstance(node, ALLOWED_NODES):
            bad(node, f"{type(node).__name__} is outside the subset")
            continue
        if isinstance(node, ast.Constant):
            if node is docstring:
                continue
            if type(node.value) not in (int, bool):
                bad(node, f"constant {node.value!r}: only integers")
        elif isinstance(node, ast.Compare):
            for op, right in zip(node.ops, node.comparators, strict=True):
                if isinstance(op, ast.In) and not (
                    isinstance(right, ast.Tuple)
                    and all(isinstance(e, ast.Constant) for e in right.elts)
                ):
                    bad(node, "'in' only against a tuple of constants")
        elif isinstance(node, ast.Return):
            if handler and node.value is not None:
                bad(node, "a handler returns nothing")
        elif isinstance(node, ast.Name):
            name = node.id
            if name == "self":
                parent = parents.get(node)
                if not method:
                    bad(node, "a pure helper does not see self")
                elif not (isinstance(parent, ast.Attribute) and parent.value is node):
                    bad(node, "self only as self.<name>")
            elif isinstance(node.ctx, ast.Load) and not (
                name in local
                or name in module.constants
                or name in module.functions
                or name in BUILTINS
            ):
                bad(node, f"name {name!r} is not a parameter, local, constant or helper")
        elif isinstance(node, ast.Attribute):
            base = node.value
            if not (isinstance(base, ast.Name) and base.id == "self"):
                bad(node, f"attribute .{node.attr} of something other than self")
                continue
            attr = node.attr
            parent = parents.get(node)
            called = isinstance(parent, ast.Call) and parent.func is node
            if called:
                if attr in BUS or attr in CONTROL or attr in HELPERS:
                    fp.calls.add(attr)
                else:
                    bad(node, f"self.{attr}() is not a bus method, _enter or a helper")
            elif isinstance(node.ctx, ast.Store):
                if attr not in WRITABLE or attr == "R":
                    bad(node, f"self.{attr} may not be assigned")
                fp.writes.add(attr)
            else:
                if attr in READABLE:
                    fp.reads.add(attr)
                elif attr in WRITABLE:
                    # Read back only inside an augmented assignment of the same name.
                    if not isinstance(parent, ast.AugAssign):
                        bad(node, f"self.{attr} is write-only")
                    fp.writes.add(attr)
                else:
                    bad(node, f"self.{attr} is not named state")
                if isinstance(parent, ast.Subscript) and parent.value is node:
                    if attr != "R":
                        bad(node, f"only R is indexed, not self.{attr}")
                    elif isinstance(parent.ctx, ast.Store):  # also an AugAssign target
                        fp.writes.add("R")
                if isinstance(parent, ast.AugAssign) and parent.target is node:
                    fp.writes.add(attr)
        elif isinstance(node, ast.Subscript):
            base = node.value
            is_r = (isinstance(base, ast.Name) and base.id in aliases) or (
                isinstance(base, ast.Attribute)
                and isinstance(base.value, ast.Name)
                and base.value.id == "self"
                and base.attr == "R"
            )
            if not is_r:
                bad(node, "only R is indexed")
            elif isinstance(base, ast.Name):
                fp.reads.add("R")
                if isinstance(node.ctx, ast.Store):
                    fp.writes.add("R")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id not in module.functions and func.id not in BUILTINS:
                    bad(node, f"call of {func.id!r}: not a pure helper of this module")
                elif func.id in module.functions:
                    fp.calls.add(func.id)
            elif not isinstance(func, ast.Attribute):
                bad(node, "calls only by name")
        elif isinstance(node, ast.Assign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in module.constants:
                    bad(node, f"assigns the module constant {target.id}")
    return errors, fp


def _accesses(fn: ast.FunctionDef, module: Module) -> int:
    """The most bus accesses (MAs) any path through ``fn`` makes, helpers included."""

    def calls_in(node: ast.AST) -> int:
        total = 0
        for n in ast.walk(node):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                name = n.func.attr
                if name in BUS:
                    total += BUS[name]
                elif name in CONTROL:
                    total += CONTROL[name]
                elif name in HELPERS:
                    total += _accesses(*reversed(_find(name)))
        return total

    def block(statements: list[ast.stmt]) -> int:
        total = 0
        for statement in statements:
            if isinstance(statement, ast.If):
                total += calls_in(statement.test)
                total += max(block(statement.body), block(statement.orelse))
            else:
                total += calls_in(statement)
        return total

    return block(fn.body)


def _closure(name: str) -> tuple[list[str], Footprint]:
    """Lint a handler and every helper it reaches; its footprint, helpers folded in."""
    module, fn = _find(name)
    errors, fp = check_function(fn, module, method=True, handler=True)
    seen: set[str] = set()
    pending = list(fp.calls)
    while pending:
        callee = pending.pop()
        if callee in seen or callee in BUS or callee in CONTROL:
            continue
        seen.add(callee)
        if callee in HELPERS:
            helper_module, helper = _find(callee)
            more, sub = check_function(helper, helper_module, method=True, handler=False)
        else:
            helper = module.functions[callee]
            more, sub = check_function(helper, module, method=False, handler=False)
        errors += more
        fp.reads |= sub.reads
        fp.writes |= sub.writes
        pending += sub.calls
        fp.calls |= sub.calls
    return errors, fp


HANDLERS = sorted({form.handler for form in FORMS} | {"_illegal"})
PLAN_OF = {form.handler: PLANS[form.pipeline] for form in FORMS} | {"_illegal": PLANS["illegal"]}


@pytest.mark.parametrize("name", HANDLERS)
def test_handler_keeps_to_the_subset(name):
    errors, _ = _closure(name)
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize("name", HANDLERS)
def test_handler_effects_belong_to_its_class(name):
    _, fp = _closure(name)
    for effect, owners in EFFECT_OWNERS.items():
        touched = effect in fp.writes or effect in fp.calls
        assert not touched or name in owners, f"{name} touches {effect}"


@pytest.mark.parametrize("name", HANDLERS)
def test_handler_accesses_fit_its_ma_slots(name):
    module, fn = _find(name)
    assert _accesses(fn, module) <= len(PLAN_OF[name].mas), name


def test_every_helper_is_used_and_no_handler_is_missed():
    reached: set[str] = set()
    for name in HANDLERS:
        reached |= _closure(name)[1].calls
    assert reached >= HELPERS
    names = {n for module in MODULE_FILES.values() for n in module.methods}
    assert {n for n in names if n.startswith("_op_")} <= set(HANDLERS)


# -- the lint proves it can fail ------------------------------------------------------------

_PRELUDE = "M32 = 0xFFFFFFFF\nT = 1\n\ndef _s32(v):\n    return v\n\n"


def _lint_source(body: str) -> list[str]:
    source = _PRELUDE + "class X:\n    def _op_x(self, op):\n" + body
    tree = ast.parse(source)
    module = Module(Path("seeded.py"), tree)
    errors, _ = check_function(module.methods["_op_x"], module, method=True, handler=True)
    return errors


def test_the_lint_passes_a_clean_handler():
    assert not _lint_source(
        "        R = self.R\n"
        "        n = (op >> 8) & 15\n"
        "        R[n] = (R[n] + _s32(self._read32(R[0]))) & M32\n"
        "        self.sr = (self.sr | T) if R[n] == 0 else (self.sr & ~T)\n"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "        for i in range(4):\n            self.R[i] = 0\n",  # a loop
        "        print(op)\n",  # I/O
        "        getattr(self, '_op_nop')(op)\n",  # dynamic dispatch
        "        self._table[op](self, op)\n",  # dispatch through a table
        "        self.R[0] = self.R[1] // 3\n",  # division
        "        self.clock += 1\n",  # timing written by a handler
        "        if self.cache.enabled:\n            pass\n",  # reaching into a module
        "        self.observe(0, 8, 0)\n",  # a host hook
        "        self._bus_read(0, 8, 0)\n",  # below the bus methods
        "        self._ma_next()\n",  # the scheduler
        "        self.R = [0] * 16\n",  # rebinding the register file
        "        x = self.delay_target\n",  # reading write-only state
        "        self.R[0] = UNKNOWN\n",  # a name from nowhere
        "        self.R[0] = len('ab')\n",  # a builtin outside the list
        "        self.gbr = 1.5\n",  # a float
        "        self.R[0] = [op][0]\n",  # a list
        "        self.R[0] = (lambda: op)()\n",  # a lambda
        "        return 1\n",  # a handler's return value
        "        global T\n        T = 2\n",  # hidden state
    ],
)
def test_the_lint_catches_a_seeded_fault(fault):
    assert _lint_source(fault), fault


def _footprints() -> list[str]:
    lines = []
    for name in HANDLERS:
        _, fp = _closure(name)
        module, fn = _find(name)
        bus = sorted(fp.calls & (BUS.keys() | CONTROL.keys()))
        lines.append(
            f"{name:28} reads {','.join(sorted(fp.reads)) or '-':28} "
            f"writes {','.join(sorted(fp.writes)) or '-':22} "
            f"bus {','.join(bus) or '-'} ({_accesses(fn, module)} MA)"
        )
    return lines


if __name__ == "__main__":
    print("\n".join(_footprints()))
    sys.exit(0)
