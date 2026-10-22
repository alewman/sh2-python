# MAME as a detector: the verified recipe

MAME 0.285 (`/usr/games/mame`, the release pinned as `reference/src/mame` at tag
`mame0285`) runs the 32X with both SH-2s, deterministically, and its debugger
can log an SH-2's registers before every instruction and every access it makes
to its own on-chip modules. That is what rungs 1 and 2 need from it. It is a
**detector, never a judge** ([validation.md](validation.md)):

- **no cache**: `sh7604.cpp` maps the data array only in a comment
  (`//  map(0xc0000000, 0xc0000fff).ram(); // cache data array`), so cache
  contents, purges, two-way RAM and every miss/hit timing are absent;
- **timing is a count, not a pipeline**: the interpreter decrements `icount`
  once per instruction plus fixed extras (`sh2.cpp` `execute_run`);
- **DIVU** finishes at once and gets overflow results wrong (U12, C4);
- the 32X board around it (MAME's `mega32x`) is not this core's concern.

## The command

```sh
python scripts/mame_trace.py "/data/emu/roms-retrobat/sega32x/BC Racers (USA).7z" --seconds 1
```

It runs MAME once per SH-2 in a fresh directory (default
`tests/mame_traces/32x-<cart stem>`, ignored), each time as

```sh
mame 32x -cart <image> -rompath "/data/emu/source/myrient.erista.me/files/MAME/ROMs (non-merged)" \
  -homepath <out> -snapshot_directory <out>/snap-<cpu> -video none -sound none -nothrottle \
  -noreadconfig -skip_gameinfo -nodrc -debug -debugger none -log -seconds_to_run <n> \
  -autoboot_script <out>/<cpu>.lua
```

and keeps each run's `error.log` as `master.log` / `slave.log`. The master's
Lua script is:

```lua
local dbg = manager.machine.debugger
dbg.visible_cpu = manager.machine.devices[":sega32x:32x_master_sh2"]
dbg:command('trace S.trace,:sega32x:32x_master_sh2,noloop,{logerror "S %X %X ... %X %d\\n",pc,sr,pr,gbr,vbr,mach,macl,r0,r1,r2,r3,r4,r5,r6,r7,r8,r9,r10,r11,r12,r13,r14,r15,totalcycles}')
dbg:command('wpset fffffe00,200,r,1,{logerror "IR %X %X %X %d\\n",wpaddr,wpsize,wpdata,totalcycles; g}')
dbg:command('wpset fffffe00,200,w,1,{logerror "IW %X %X %X %d\\n",wpaddr,wpsize,wpdata,totalcycles; g}')
dbg:command("go")
```

The `32x` romset (`32x.zip`: `32x_g_bios.bin`, `32x_m_bios.bin`,
`32x_s_bios.bin`, `32x_m_bios_sdk.bin`) must be in the rompath; it comes from
the user's MAME collection and is never downloaded
(`scripts/build_referees.py --check` reports it). Cartridges: the user's own,
`.32x`, `.zip` or `.7z`.

## What it produced (2026-10-21)

`scripts/mame_trace.py "/data/emu/roms-retrobat/sega32x/BC Racers (USA).7z"
--only master --seconds 1`: **16,118,368 instructions** (`S`), **11,784**
on-chip reads (`IR`) and **23,589** on-chip writes (`IW`), a 2.07 GB log, 2 min
7 s wall. An earlier run with the trace action alone gave the same 16,118,368
instructions, the last at `totalcycles` 22,376,639: the runs are
deterministic. The first lines are the master boot ROM at `$140`
(`MOV.L @($0068,PC),R0`, `LDC R0,SR`, ...), one cycle per instruction.

The slave's run (`--only slave`) gave **13,516,656 instructions** and 18
on-chip writes, no reads, in 1 min 45 s.

What the master's on-chip accesses show, all from code MAME runs (the boot ROM's
values are the boot ROM's, whatever MAME does with them):

- The master boot ROM programs the BSC at clocks 20–32 with longword writes
  carrying the `A55A` key in the upper half: BCR1 = $0001, BCR2 = $00A8,
  WCR = $0055 (one wait plus the WAIT pin in all four areas, no idle cycles),
  MCR = $0AB8, RTCSR = $0008, RTCNT = 0, RTCOR = $0059. This agrees with the
  bulletin's "after implementing boot ROM only external Wait is set" (32XDK
  wiki) and gives rung 2 its BSC state.
- 23,403 byte writes to TOCR ($FFFFFE17, toggling `E2`/`F2`: OLVLA), one per
  852 clocks on average: the FRT bumping of U17, from code.
- The game uses the DIVU 84 times in the second: `DVSR` then `DVDNT` written,
  and `DVDNTL`'s mirror at $FFFFFF1C read **one clock after** the start
  (`IW FFFFFF04 ... 7738069`, `IR FFFFFF1C ... 7738070`). On hardware that read
  stalls until the 39-state division ends (HM 10.4.1); MAME answers at once.
  Real code depends on the stall, so the core must model it.

## The traps

1. **The SH-2 recompiler crashes MAME 0.285 under a trace.** With the DRC on
   (the default), `trace` on either 32X SH-2 segfaults at the same instruction
   every time (BC Racers: after 1,923,531 traced instructions, master
   `totalcycles` 1,863,969), whether or not the trace has an action. Without a
   trace the DRC runs; with `-nodrc` the traced run completes. Every run here
   passes `-nodrc`. Found 2026-10-21.
2. **The device tags are `:sega32x:32x_master_sh2` and `:sega32x:32x_slave_sh2`.**
   `-listdevices 32x` prints them indented under the `sega32x` device,
   without the prefix; `manager.machine.devices[":32x_master_sh2"]`
   is nil, and setting `visible_cpu` to nil segfaults MAME at once. List the
   tags from Lua (`for tag in pairs(manager.machine.devices)`) when in doubt.
3. **Actions are evaluated against the debugger's visible CPU, not the traced
   one** (megadrive-python's `docs/mame-oracle.md`, trap 1), so each SH-2 gets
   its own run with itself visible; for a cartridge without input the runs are
   the same run.
4. **24 values in one `logerror` work for the SH-2** (23 registers and
   `totalcycles`); megadrive-python found a 14-value limit on the Z80 action,
   which does not apply here.
5. **`totalcycles` is the SH-2's own clock** (23.01 MHz), and consecutive
   instructions can show the same count when MAME charges an instruction's
   extra cycles in a block: sub-instruction timing cannot be read from these
   logs, and per-instruction counts only approximately.
6. **Access lines carry the instruction's cycle, not the bus cycle's**, as for
   the other cores' MAME recipes; `wpsize` prints in bits (`8`, `20` hex for a
   longword).
7. **Speed**: 64,000 to 127,000 instructions per wall second with `-nodrc` and
   the trace (251 s and 127 s for the same second on a loaded and an idle
   machine), so one emulated second of one SH-2 is 2–4 minutes and 2 GB. Keep runs
   short; trace a window with the debugger's `trace` on/off commands when a
   later rung needs a late part of a game.
