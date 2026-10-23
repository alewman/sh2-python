//! Run rung-1 corpus cases on jgenesis' `sh2-emu` (validation/rung1/README.md).
//!
//! stdin: one case per line, the format `validation/rung1/corpus.py` writes:
//!
//!     C <id> <seed> <pc> <sr> <gbr> <vbr> <mach> <macl> <pr> <r0> .. <r15> W <a:w,...> L <a,...>
//!
//! (hexadecimal but the id). Memory is two regions, CODE at H'0600F000 (H'2400 bytes)
//! and DATA at H'06020000 (H'1000 bytes), filled from `seed` by xorshift32 and then
//! patched with the `W` words; the CPU state is set and the core stepped until PC is
//! one of the `L` landing addresses outside a delay slot (at most 16 steps).
//!
//! stdout: one result per case:
//!
//!     R <id> <status> <pc> <sr> <gbr> <vbr> <mach> <macl> <pr> <r0> .. <r15> M <a:b,...>
//!
//! `status` is `ok`, `timeout` (no landing reached) or `outside` (an access left the
//! two regions); `M` lists every byte that differs from the initial image.

use sh2_emu::Sh2;
use sh2_emu::bus::{AccessContext, BusInterface, OpSize};
use sh2_emu::debug::DummySh2Debugger;
use std::io::{self, BufRead, Write};

const CODE_BASE: u32 = 0x0600_F000;
const CODE_SIZE: usize = 0x2400;
const DATA_BASE: u32 = 0x0602_0000;
const DATA_SIZE: usize = 0x1000;

struct Memory {
    code: Vec<u8>,
    data: Vec<u8>,
    outside: bool,
    armed: bool,
}

impl Memory {
    fn slot(&mut self, address: u32) -> Option<&mut [u8]> {
        let a = address & 0x1FFF_FFFF;
        if (CODE_BASE..CODE_BASE + CODE_SIZE as u32).contains(&a) {
            return Some(&mut self.code[(a - CODE_BASE) as usize..]);
        }
        if (DATA_BASE..DATA_BASE + DATA_SIZE as u32).contains(&a) {
            return Some(&mut self.data[(a - DATA_BASE) as usize..]);
        }
        if self.armed {
            self.outside = true;
        }
        None
    }

    fn get(&mut self, address: u32, bytes: usize) -> u32 {
        match self.slot(address) {
            Some(s) if s.len() >= bytes => {
                s[..bytes].iter().fold(0u32, |acc, &b| (acc << 8) | u32::from(b))
            }
            Some(_) => {
                self.outside = true;
                0
            }
            None => 0,
        }
    }

    fn put(&mut self, address: u32, bytes: usize, value: u32) {
        let mut outside = false;
        if let Some(s) = self.slot(address) {
            if s.len() >= bytes {
                for i in 0..bytes {
                    s[i] = (value >> (8 * (bytes - 1 - i))) as u8;
                }
            } else {
                outside = true;
            }
        }
        if outside {
            self.outside = true;
        }
    }
}

fn size_bytes<const SIZE: u8>() -> usize {
    match SIZE {
        OpSize::BYTE => 1,
        OpSize::WORD => 2,
        _ => 4,
    }
}

impl BusInterface for Memory {
    type DebugView<'a> = DummySh2Debugger;

    fn read<const SIZE: u8>(&mut self, address: u32, _ctx: AccessContext) -> u32 {
        self.get(address, size_bytes::<SIZE>())
    }

    fn read_cache_line(&mut self, address: u32, _ctx: AccessContext) -> [u16; 8] {
        let base = address & !0xF;
        let mut line = [0u16; 8];
        for (i, word) in line.iter_mut().enumerate() {
            *word = self.get(base + 2 * i as u32, 2) as u16;
        }
        line
    }

    fn write<const SIZE: u8>(&mut self, address: u32, value: u32, _ctx: AccessContext) {
        self.put(address, size_bytes::<SIZE>(), value);
    }

    fn reset(&self) -> bool {
        false
    }

    fn interrupt_level(&self) -> u8 {
        0
    }

    fn dma_request_0(&self) -> bool {
        false
    }

    fn dma_request_1(&self) -> bool {
        false
    }

    fn acknowledge_dreq_1(&mut self) {}

    fn serial_rx(&mut self) -> Option<u8> {
        None
    }

    fn serial_tx(&mut self, _value: u8) {}

    fn increment_cycle_counter(&mut self, _cycles: u64) {}

    fn should_stop_execution(&self) -> bool {
        false
    }

    sh2_emu::bus::impl_sh2_opcode_table!(Memory);
}

fn xorshift_fill(buffer: &mut [u8], seed: u32) {
    let mut x = if seed == 0 { 0x1234_5678 } else { seed };
    for chunk in buffer.chunks_mut(4) {
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        let bytes = x.to_be_bytes();
        chunk.copy_from_slice(&bytes[..chunk.len()]);
    }
}

fn hex(token: &str) -> u32 {
    u32::from_str_radix(token, 16).expect("hexadecimal field")
}

fn run_case(line: &str) -> String {
    let fields: Vec<&str> = line.split_whitespace().collect();
    assert_eq!(fields[0], "C", "a case line starts with C");
    let id = fields[1];
    let seed = hex(fields[2]);
    let regs: Vec<u32> = fields[3..26].iter().map(|t| hex(t)).collect();
    let w_at = fields.iter().position(|&t| t == "W").expect("W");
    let l_at = fields.iter().position(|&t| t == "L").expect("L");
    let writes: Vec<(u32, u32)> = fields[w_at + 1..l_at]
        .iter()
        .flat_map(|t| t.split(','))
        .filter(|t| !t.is_empty())
        .map(|t| {
            let (a, w) = t.split_once(':').expect("address:word");
            (hex(a), hex(w))
        })
        .collect();
    let landings: Vec<u32> = fields[l_at + 1..]
        .iter()
        .flat_map(|t| t.split(','))
        .filter(|t| !t.is_empty())
        .map(hex)
        .collect();

    let mut memory = Memory {
        code: vec![0; CODE_SIZE],
        data: vec![0; DATA_SIZE],
        outside: false,
        armed: false,
    };
    xorshift_fill(&mut memory.code, seed);
    xorshift_fill(&mut memory.data, seed ^ 0x9E37_79B9);
    for &(address, word) in &writes {
        memory.put(address, 2, word);
    }
    let image_code = memory.code.clone();
    let image_data = memory.data.clone();

    let mut cpu = Sh2::new("rung1".into());
    cpu.execute(1, &mut memory); // the reset: reads the vectors (outside, not armed)
    {
        let r = cpu.registers_mut();
        r.gpr.copy_from_slice(&regs[7..23]);
        r.sr = regs[1].into();
        r.gbr = regs[2];
        r.vbr = regs[3];
        r.mach = regs[4];
        r.macl = regs[5];
        r.pr = regs[6];
        r.next_op_in_delay_slot = false;
    }
    cpu.set_pc(regs[0]);
    memory.armed = true;

    let mut status = "timeout";
    for _ in 0..16 {
        cpu.execute(1, &mut memory);
        let r = cpu.registers();
        if !r.next_op_in_delay_slot && landings.contains(&r.pc) {
            status = "ok";
            break;
        }
    }
    if memory.outside {
        status = "outside";
    }
    let r = cpu.registers();
    let mut out = format!(
        "R {id} {status} {:X} {:X} {:X} {:X} {:X} {:X} {:X}",
        r.pc,
        u32::from(r.sr),
        r.gbr,
        r.vbr,
        r.mach,
        r.macl,
        r.pr
    );
    for value in r.gpr {
        out.push_str(&format!(" {value:X}"));
    }
    out.push_str(" M ");
    let mut diffs = Vec::new();
    for (base, now, before) in [
        (CODE_BASE, &memory.code, &image_code),
        (DATA_BASE, &memory.data, &image_data),
    ] {
        for (i, (a, b)) in now.iter().zip(before.iter()).enumerate() {
            if a != b {
                diffs.push(format!("{:X}:{a:X}", base + i as u32));
            }
        }
    }
    out.push_str(&diffs.join(","));
    out
}

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();
    if std::env::args().nth(1).as_deref() == Some("--disasm") {
        // jgenesis' disassembly of every 16-bit word at address 0, one per line, for
        // the decode-table cross-check (tests/test_disasm.py).
        let mut instruction = sh2_emu::DisassembledInstruction::new();
        for opcode in 0..=u16::MAX {
            sh2_emu::disassemble_into(0, opcode, &mut instruction);
            writeln!(out, "{opcode:04X} {}", instruction.text).expect("stdout");
        }
        return;
    }
    for line in stdin.lock().lines() {
        let line = line.expect("stdin");
        if line.trim().is_empty() {
            continue;
        }
        writeln!(out, "{}", run_case(&line)).expect("stdout");
    }
}
