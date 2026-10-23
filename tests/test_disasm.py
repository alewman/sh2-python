"""The disassembler and the decode table against two other decoders, word by word.

Two machine-readable decoders, run on all 65,536 words (docs/start-here.md: "check it
against two of these"):

- ``tests/data/disasm_mame.txt.gz``: MAME 0.285's ``sh_dasm.cpp``, as
  ``unidasm <every word> -arch sh2 -norawbytes`` printed it (each word at address 2 x
  word);
- ``tests/data/disasm_jgenesis.txt.gz``: jgenesis' ``disassemble_into`` (sh2-emu @
  d41e2d97), from ``sh2-jgenesis-harness --disasm`` (scripts/build_jgenesis_harness.py),
  each word at address 0.

jgenesis' disassembler calls exactly the same 11,784 words undefined as PM appendix A
does. MAME's *disassembler* decodes some groups on fewer bits than PM (its 0000 and
0100 groups ignore bits 7-6), so it names words PM leaves undefined; MAME's
*interpreter* runs them that way too, which rung 1 records as the ``undefined-code``
ruling. Where both define a word, the texts match except for MAME's spellings listed
in ``MAME_SPELLINGS``.
"""

import gzip
import re
from pathlib import Path

from sh2_python._decode import decode_index
from sh2_python.disasm import disassemble_word

DATA = Path(__file__).resolve().parent / "data"
INDEX = decode_index()

#: MAME's text -> PM's, for the forms MAME spells its own way.
MAME_SPELLINGS = (
    (re.compile(r"^BTS "), "BT/S "),
    (re.compile(r"^BFS "), "BF/S "),
    (re.compile(r"^STS GBR,"), "STC GBR,"),
    (re.compile(r"^JMP (R\d+)$"), r"JMP @\1"),
    (re.compile(r"^JSR (R\d+)$"), r"JSR @\1"),
    (re.compile(r"^TAS (R\d+)$"), r"TAS.B @\1"),
)


def _lines(name: str) -> list[str]:
    with gzip.open(DATA / name, "rt") as handle:
        return handle.read().splitlines()


def test_jgenesis_defines_exactly_the_words_pm_defines():
    lines = _lines("disasm_jgenesis.txt.gz")
    assert len(lines) == 0x10000
    for line in lines:
        word, _, text = line.partition(" ")
        opcode = int(word, 16)
        form = INDEX[opcode]
        if text == "illegal":
            assert form is None, line
            continue
        assert form is not None, line
        theirs = text.split()[0].upper()
        if form.mnemonic == "MOV" and form.operands == "#imm,Rn":
            assert theirs == "MOV.B"  # jgenesis writes MOV #imm,Rn as mov.b
        else:
            assert theirs == form.mnemonic, line


def test_text_matches_mame_where_both_define_the_word():
    lines = _lines("disasm_mame.txt.gz")
    assert len(lines) == 0x10000
    mame_only = 0
    for opcode, line in enumerate(lines):
        theirs = re.sub(r"\s+", " ", line.split(":", 1)[1].strip())
        undefined = theirs.split()[0] in ("??????", "invalid", "unknown")
        if INDEX[opcode] is None:
            if not undefined:
                mame_only += 1
                # MAME's looser decode: the 0000 and 0100 groups only.
                assert opcode >> 12 in (0x0, 0x4), line
            continue
        assert not undefined, line
        for pattern, replacement in MAME_SPELLINGS:
            theirs = pattern.sub(replacement, theirs)
        ours = re.sub(r"\s+", " ", disassemble_word(opcode, opcode * 2).text)
        assert ours == theirs, (hex(opcode), ours, theirs)
    assert mame_only == 2760  # words MAME names that PM leaves undefined


def test_undefined_words_disassemble_as_data():
    assert disassemble_word(0xFFFF).text == ".word   $FFFF"
