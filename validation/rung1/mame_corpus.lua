-- Rung 1 on MAME 0.285: run corpus cases on the 32X's master SH-2, one per frame.
--
-- Started by validation/rung1/run_mame.py as MAME's autoboot script with
--   mame 32x -cart <any 32X cartridge> -nodrc ... -autoboot_script mame_corpus.lua
-- and SH2_CORPUS_IN / SH2_CORPUS_OUT naming the case and result files (the format of
-- validation/rung1/corpus.py). No debugger: the script uses MAME's Lua state and
-- address-space API between frames.
--
-- At the second frame both SH-2s have left reset. The slave is parked on a
-- branch-to-itself in SDRAM with SR.I = 15; the master's 32X interrupt sources are
-- masked at the 32X (H'20004000 = 0) and at its INTC (IPRA = IPRB = 0), and its DMAC
-- channels stopped, so nothing interrupts the cases (they may set SR.I < 15).
-- Then, each frame: read the previous case's result (the master is spinning on its
-- landing loop, BRA $ + NOP, with every register as the case left it but PC, which is
-- the landing or the landing + 2), then write the next case's memory and registers
-- and let the frame run it. Setting CURPC also clears MAME's pending delay slot
-- (sh2.cpp state_import), so a case never inherits the previous loop's branch.

local master = manager.machine.devices[":sega32x:32x_master_sh2"]
local slave = manager.machine.devices[":sega32x:32x_slave_sh2"]
local space = master.spaces["program"]
local sspace = slave.spaces["program"]

local CODE_BASE, CODE_SIZE = 0x0600F000, 0x2400
local DATA_BASE, DATA_SIZE = 0x06020000, 0x1000
local PARK = 0x0603F000
local M32 = 0xFFFFFFFF

local input = assert(io.open(os.getenv("SH2_CORPUS_IN"), "r"))
local output = assert(io.open(os.getenv("SH2_CORPUS_OUT"), "w"))

local REGS = { "PC", "SR", "GBR", "VBR", "MACH", "MACL", "PR" }
for i = 0, 15 do REGS[#REGS + 1] = "R" .. i end

local function fill(base, size, seed)
  local x = seed
  if x == 0 then x = 0x12345678 end
  local image = {}
  for i = 0, size - 4, 4 do
    x = x ~ ((x << 13) & M32)
    x = x ~ (x >> 17)
    x = x ~ ((x << 5) & M32)
    image[i // 4] = x
  end
  return image
end

local current = nil  -- { id, landings = {}, code = image, data = image }

local function split(text, sep)
  local out = {}
  for part in string.gmatch(text, "[^" .. sep .. "]+") do out[#out + 1] = part end
  return out
end

local function start_case(line)
  local fields = split(line, " ")
  local id, seed = fields[2], tonumber(fields[3], 16)
  local values = {}
  for i = 4, 26 do values[#values + 1] = tonumber(fields[i], 16) end
  local words, landings = {}, {}
  local mode = nil
  for i = 27, #fields do
    if fields[i] == "W" or fields[i] == "L" then
      mode = fields[i]
    elseif mode == "W" then
      for _, pair in ipairs(split(fields[i], ",")) do
        local a, w = string.match(pair, "(%x+):(%x+)")
        words[#words + 1] = { tonumber(a, 16), tonumber(w, 16) }
      end
    elseif mode == "L" then
      for _, a in ipairs(split(fields[i], ",")) do landings[tonumber(a, 16)] = true end
    end
  end
  local code = fill(CODE_BASE, CODE_SIZE, seed)
  local data = fill(DATA_BASE, DATA_SIZE, seed ~ 0x9E3779B9)
  -- Apply the words to the images, then write the images.
  for _, pw in ipairs(words) do
    local a, w = pw[1], pw[2]
    local image, base = code, CODE_BASE
    if a >= DATA_BASE then image, base = data, DATA_BASE end
    local index = (a - base) // 4
    if (a & 2) == 0 then
      image[index] = (image[index] & 0x0000FFFF) | (w << 16)
    else
      image[index] = (image[index] & 0xFFFF0000) | w
    end
  end
  for i = 0, CODE_SIZE // 4 - 1 do space:write_u32(CODE_BASE + 4 * i, code[i]) end
  for i = 0, DATA_SIZE // 4 - 1 do space:write_u32(DATA_BASE + 4 * i, data[i]) end
  local state = master.state
  for i = 0, 15 do state["R" .. i].value = values[8 + i] end
  state["GBR"].value = values[3]
  state["VBR"].value = values[4]
  state["MACH"].value = values[5]
  state["MACL"].value = values[6]
  state["PR"].value = values[7]
  state["SR"].value = values[2]
  state["CURPC"].value = values[1]
  current = { id = id, landings = landings, code = code, data = data }
end

local function finish_case()
  local state = master.state
  local pc = state["CURPC"].value
  local status = "ok"
  if not current.landings[pc] then
    if current.landings[pc - 2] then pc = pc - 2 else status = "timeout" end
  end
  local out = { "R", current.id, status, string.format("%X", pc) }
  for i = 2, #REGS do out[#out + 1] = string.format("%X", state[REGS[i]].value & M32) end
  out[#out + 1] = "M"
  local diffs = {}
  for _, region in ipairs({ { CODE_BASE, CODE_SIZE, current.code }, { DATA_BASE, DATA_SIZE, current.data } }) do
    local base, size, image = region[1], region[2], region[3]
    for i = 0, size // 4 - 1 do
      local now = space:read_u32(base + 4 * i)
      local was = image[i]
      if now ~= was then
        for b = 0, 3 do
          local shift = 24 - 8 * b
          local nb, wb = (now >> shift) & 0xFF, (was >> shift) & 0xFF
          if nb ~= wb then diffs[#diffs + 1] = string.format("%X:%X", base + 4 * i + b, nb) end
        end
      end
    end
  end
  output:write(table.concat(out, " ") .. " " .. table.concat(diffs, ",") .. "\n")
  current = nil
end

local frame = 0
local quiet = false
sub = emu.add_machine_frame_notifier(function()
  frame = frame + 1
  if frame < 2 then return end
  if not quiet then
    -- Park the slave; silence everything that could interrupt the master.
    sspace:write_u16(PARK, 0xAFFE)
    sspace:write_u16(PARK + 2, 0x0009)
    slave.state["SR"].value = 0xF0
    slave.state["CURPC"].value = PARK
    space:write_u16(0x20004000, 0)       -- 32X interrupt mask (SH-2 side)
    space:write_u16(0xFFFFFEE2, 0)       -- IPRA
    space:write_u16(0xFFFFFE60, 0)       -- IPRB
    space:write_u32(0xFFFFFF8C, 0)       -- CHCR0
    space:write_u32(0xFFFFFF9C, 0)       -- CHCR1
    space:write_u32(0xFFFFFFB0, 0)       -- DMAOR
    quiet = true
  end
  if current then finish_case() end
  local line = input:read("l")
  while line and string.sub(line, 1, 1) ~= "C" do line = input:read("l") end
  if not line then
    output:close()
    manager.machine:exit()
    return
  end
  start_case(line)
end)
