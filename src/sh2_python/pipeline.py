"""The pipeline classes of PM section 7, and the seam where the stage scheduler goes.

Every decode-table form names one of the classes below (``_decode.py``). A class is PM
table 7.2's row for it: the stages one instruction occupies in the five-stage pipeline
(IF ID EX MA WB, plus ``mm`` for the multiplier's busy states), the execution states
without contention, how many slots the *next* instruction's ID stalls, and the figure
in PM 7.7 that draws it. The SH-2 numbers are used throughout (PM table 7.2 note 1:
the SH-1's multiplier stages differ).

**What runs today (rungs 0-2).** ``step()`` charges each instruction the minimum
states of PM table A.50 (``Form.states``: the not-taken count for conditional branches,
plus 2 when ``BT``/``BF`` is taken and 1 when ``BT/S``/``BF/S`` is taken), plus, for
every external bus cycle, its length beyond the one state the table assumes (PM 7.3:
"the execution states shown in the table are minimums" with one-state memory). That
is the per-instruction count the brief allows before rung 3, not the pipeline.

**What rung 3 replaces.** :class:`TableTiming` below is the seam: the core asks it for
an instruction's base states once per step. The stage-level scheduler (IF/MA
contention and slot splits, PM 7.4; load-use, PM 7.5; the multiplier's ``mm`` and ID
stalls, PM 7.7.2; branch overrun fetches, PM 7.7.5; the exception sequences, PM 7.7.7)
will implement the same interface from :data:`CLASSES`, and the figures of PM 7.4-7.7
become its tests. Nothing here pretends to be that scheduler.
"""

from typing import NamedTuple


class PipelineClass(NamedTuple):
    """One row of PM table 7.2, SH-2 column."""

    name: str
    category: str  # PM table 7.2's "Category" text
    stages: tuple[str, ...]  # the instruction's own stages, as the figure draws them
    states: int  # execution states with no contention (table 7.2 "State")
    next_id_stall: int  # slots the following instruction's ID waits (the figure)
    mm: int  # multiplier states after the last MA (PM 7.7.2)
    figure: str


def _c(name, category, stages, states, stall, mm, figure) -> PipelineClass:
    return PipelineClass(name, category, tuple(stages.split()), states, stall, mm, figure)


CLASSES: dict[str, PipelineClass] = {
    c.name: c
    for c in (
        _c("transfer", "Register-register transfer", "IF ID EX", 1, 0, 0, "PM figure 7.10"),
        _c("load", "Memory load", "IF ID EX MA WB", 1, 0, 0, "PM figure 7.11"),
        _c("store", "Memory store", "IF ID EX MA", 1, 0, 0, "PM figure 7.12"),
        _c("arith", "Arithmetic between registers", "IF ID EX", 1, 0, 0, "PM figure 7.13"),
        _c("mac_w", "Multiply/accumulate", "IF ID EX MA MA mm mm", 3, 1, 2, "PM figure 7.23"),
        _c("mac_l", "Double-length multiply/accumulate", "IF ID EX MA MA mm mm mm mm", 3, 1, 4,
           "PM figure 7.34"),
        _c("mul_w", "Multiplication", "IF ID EX MA mm mm", 1, 0, 2, "PM figure 7.53"),
        _c("mul_l", "Double-length multiplication", "IF ID EX MA MA mm mm mm mm", 2, 1, 4,
           "PM figure 7.63"),
        _c("logic", "Register-register logic operation", "IF ID EX", 1, 0, 0, "PM figure 7.74"),
        _c("logic_mem", "Memory logic operation", "IF ID EX MA EX MA", 3, 2, 0, "PM figure 7.75"),
        _c("tas", "TAS", "IF ID EX MA EX MA", 4, 3, 0, "PM figure 7.76"),
        _c("shift", "Shift", "IF ID EX", 1, 0, 0, "PM figure 7.77"),
        _c("branch_cond", "Conditional branch", "IF ID EX", 1, 0, 0, "PM figures 7.78, 7.79"),
        _c("branch_cond_delayed", "Delayed conditional branch", "IF ID EX", 1, 0, 0,
           "PM figures 7.80, 7.81"),
        _c("branch", "Unconditional branch", "IF ID EX", 2, 1, 0, "PM figure 7.82"),
        _c("system", "System control ALU", "IF ID EX", 1, 0, 0, "PM figure 7.83"),
        _c("ldc_l", "LDC.L", "IF ID EX MA EX", 3, 2, 0, "PM figure 7.84"),
        _c("stc_l", "STC.L", "IF ID EX MA", 2, 1, 0, "PM figure 7.85"),
        _c("lds_l_pr", "LDS.L (PR)", "IF ID EX MA WB", 1, 0, 0, "PM figure 7.86"),
        _c("sts_l_pr", "STS.L (PR)", "IF ID EX MA", 1, 0, 0, "PM figure 7.87"),
        _c("reg_to_mac", "Register to MAC transfer", "IF ID EX MA", 1, 0, 0, "PM figure 7.88"),
        _c("mem_to_mac", "Memory to MAC transfer", "IF ID EX MA", 1, 0, 0, "PM figure 7.89"),
        _c("mac_to_reg", "MAC to register transfer", "IF ID EX MA WB", 1, 0, 0,
           "PM figure 7.90"),
        _c("mac_to_mem", "MAC to memory transfer", "IF ID EX MA", 1, 0, 0, "PM figure 7.91"),
        _c("rte", "RTE", "IF ID EX MA MA", 4, 3, 0, "PM figure 7.92"),
        _c("trap", "TRAP", "IF ID EX EX MA MA MA EX EX", 8, 0, 0, "PM figure 7.93"),
        _c("sleep", "SLEEP", "IF ID EX", 3, 0, 0, "PM figure 7.94"),
    )
}  # fmt: skip

#: The exception sequences of PM 7.7.7. ``states`` is not in PM table 7.2; it is
#: counted here the way table 7.2 counts TRAPA (nine stages, eight states: one state
#: per stage after ID, plus one), a derivation rung 3's figures will check.
EXCEPTIONS: dict[str, PipelineClass] = {
    "interrupt": _c("interrupt", "Interrupt exception processing",
                    "IF ID EX EX MA MA EX MA EX EX", 9, 0, 0, "PM figure 7.95"),
    "address_error": _c("address_error", "Address error exception processing",
                        "IF ID EX EX MA MA EX MA EX EX", 9, 0, 0, "PM figure 7.96"),
    "illegal": _c("illegal", "Illegal instruction exception processing",
                  "IF ID EX EX MA MA MA EX EX", 8, 0, 0, "PM figure 7.97"),
}  # fmt: skip

#: Extra states of a taken conditional branch over its not-taken 1 (PM table A.50: BT/BF
#: 3/1, BT/S/BF/S 2/1).
TAKEN_EXTRA = {"branch_cond": 2, "branch_cond_delayed": 1}


class TableTiming:
    """Per-instruction states from the manual's tables: the rung 0-2 timing model.

    ``base_states[opcode]`` is what ``step()`` charges an instruction before bus
    waits; the stage scheduler of rung 3 replaces this object, keeping the attribute.
    """

    def __init__(self, base_states: list[int]) -> None:
        self.base_states = base_states


__all__ = ["CLASSES", "EXCEPTIONS", "TAKEN_EXTRA", "PipelineClass", "TableTiming"]
