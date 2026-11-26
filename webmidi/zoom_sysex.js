// zoom_sysex.py for the browser, as far as the Web MIDI prototype needs it: the same builders,
// the same allowlist for what it sends (stricter: every other kind is refused), the same parsers.
// tests/test_webmidi.py runs this file in Node and compares its bytes with the Python's.

export const SOX = 0xF0, EOX = 0xF7, ZOOM = 0x52;
export const IDENTITY_REQUEST = Uint8Array.of(SOX, 0x7E, 0x7F, 0x06, 0x01, EOX);

// The kinds this file can send, in the form zoom_sysex.KINDS shows them for approval.
export const KINDS = {
  identity_request: "F0 7E 7F 06 01 F7",
  edit_enable: "F0 52 00 <ID> 50 F7",
  edit_disable: "F0 52 00 <ID> 51 F7",
  set_param: "F0 52 00 <ID> 64 20 00 <slot> <param> <LSB> <MSB> 00 00 00 F7",
  query_program: "F0 52 00 <ID> 33 F7",
  query_patch: "F0 52 00 <ID> 64 13 F7",
};
// The same kinds in plain words (zoom_sysex.KIND_LABELS), for the approvals on the page.
export const LABELS = {
  identity_request: "Find the pedal and read its model and firmware",
  edit_enable: "Switch on the pedal's editing mode, so that it reports turned knobs",
  edit_disable: "Switch the editing mode off again when disconnecting",
  set_param: "Change knob values of the preset on the pedal",
  query_program: "Ask which preset is selected",
  query_patch: "Read the effects and knob values of the preset on the pedal",
};
const FIXED_BODIES = {"50": "edit_enable", "51": "edit_disable", "33": "query_program", "64 13": "query_patch"};
const FORBIDDEN_PREFIXES = [[0x01], [0x04], [0x5B], [0x28], [0x60], [0x64, 0x47], [0x64, 0x20, 0x00, 0x64],
                            [0x32], [0x45]];
const RESERVED_SLOTS = [0x5F, 0x64];
export const MIN_PARAM = 2;
export const MAX_SLOTS = 6;
export const EDTB_WIDTHS = [12, 12, 12, 12, 12, 8, 8, 8, 12, 8, 8, 8, 12, 8, 8, 8, 1, 1, 1];

export class NotAllowed extends Error { name = "NotAllowed"; }
export class NotApproved extends Error {
  name = "NotApproved";
  constructor(kind) { super(`no approval for: ${kind}`); this.kind = kind; }
}

export const toHex = (data) => Array.from(data, (b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ");
const startsWith = (data, prefix) => prefix.every((byte, index) => data[index] === byte);
const ascii = (data) => String.fromCharCode(...Array.from(data, (b) => b < 128 ? b : 0xFFFD));
const little = (data, start, size) => data.slice(start, start + size).reduceRight((sum, b) => sum * 256 + b, 0);

export function encodeValue(value) {
  if (!Number.isInteger(value) || value < 0 || value > 0x3FFF) throw new RangeError(`value ${value} does not fit into 14 bits`);
  return [value & 0x7F, value >> 7];
}
export const decodeValue = (lsb, msb) => lsb | msb << 7;

// --- Builders ---

const zoom = (deviceId, ...body) => Uint8Array.of(SOX, ZOOM, 0x00, deviceId, ...body, EOX);
export const buildIdentityRequest = () => IDENTITY_REQUEST.slice();
export const buildEditEnable = (deviceId) => zoom(deviceId, 0x50);
export const buildEditDisable = (deviceId) => zoom(deviceId, 0x51);
export const buildQueryProgram = (deviceId) => zoom(deviceId, 0x33);
export const buildQueryPatch = (deviceId) => zoom(deviceId, 0x64, 0x13);
export function buildSetParam(deviceId, slot, param, value) {
  const [lsb, msb] = encodeValue(value);
  return zoom(deviceId, 0x64, 0x20, 0x00, slot, param, lsb, msb, 0x00, 0x00, 0x00);
}

// --- Allowlist ---

// The message kind, or NotAllowed. targets: [{slot, param, min, max}], the knobs that may be set.
export function classify(msg, deviceId = null, targets = []) {
  msg = Uint8Array.from(msg);
  if (msg.length < 2 || msg[0] !== SOX || msg[msg.length - 1] !== EOX || msg.slice(1, -1).some((b) => b > 0x7F)) {
    throw new NotAllowed(`not a valid SysEx message: ${toHex(msg)}`);
  }
  if (toHex(msg) === toHex(IDENTITY_REQUEST)) return "identity_request";
  if (deviceId == null) throw new NotAllowed("device ID unknown: only the identity request is allowed");
  if (msg[1] !== ZOOM || msg[2] !== 0x00 || msg[3] !== deviceId) {
    throw new NotAllowed(`no Zoom header for device ID ${deviceId}: ${toHex(msg)}`);
  }
  const body = msg.slice(4, -1);
  for (const prefix of FORBIDDEN_PREFIXES) {
    if (startsWith(body, prefix)) throw new NotAllowed(`explicitly forbidden command ${toHex(prefix)}: ${toHex(msg)}`);
  }
  if (FIXED_BODIES[toHex(body)]) return FIXED_BODIES[toHex(body)];
  if (body.length === 10 && startsWith(body, [0x64, 0x20, 0x00]) && startsWith(body.slice(7), [0, 0, 0])) {
    const [slot, param, lsb, msb] = body.slice(3, 7);
    checkParam(slot, param, decodeValue(lsb, msb), targets);
    return "set_param";
  }
  throw new NotAllowed(`not on the allowlist: ${toHex(msg)}`);
}

function checkParam(slot, param, value, targets) {
  if (RESERVED_SLOTS.includes(slot)) throw new NotAllowed(`slot byte ${slot} is not an effect slot`);
  if (param < MIN_PARAM) throw new NotAllowed(`param ${param} is blocked (effect on/off or effect type)`);
  const target = targets.find((t) => t.slot === slot && t.param === param);
  if (!target) throw new NotAllowed(`slot ${slot}, param ${param} has not been measured`);
  if (value < target.min || value > target.max) {
    throw new NotAllowed(`value ${value} is outside the measured range ${target.min}–${target.max} (slot ${slot}, param ${param})`);
  }
}

// --- Parsers ---

// F0 7E <ch> 06 02 52 <family LSB MSB> <model LSB MSB> <version ASCII> F7
export function parseIdentityReply(msg) {
  if (!msg || msg.length < 11 || msg[1] !== 0x7E || !startsWith(msg.slice(3), [0x06, 0x02, ZOOM])) return null;
  return {deviceId: msg[6], family: [msg[6], msg[7]], model: [msg[8], msg[9]], version: ascii(msg.slice(10, -1))};
}

// model number from the identity -> the pedal's name (zoom_sysex.MODELS)
export const MODELS = {0x23: "MS-50G+", 0x25: "MS-200D+", 0x26: "MS-70CDR+", 0x27: "MS-60B+", 0x28: "MS-80IR+"};
export const modelName = (identity) => MODELS[identity.model[0] | identity.model[1] << 7] ?? null;

// The content of a Zoom SysEx between `F0 52 00 <ID>` and `F7`, otherwise null.
export function zoomBody(msg, deviceId) {
  if (!msg || msg.length < 6 || !startsWith(msg, [SOX, ZOOM, 0x00, deviceId])) return null;
  return Uint8Array.from(msg.slice(4, -1));
}

export const isAck = (body) => body != null && body.length === 2 && body[0] === 0 && body[1] === 0;

// `64 20 00|01 <slot> <param> <LSB> <MSB> …`: a knob changed on the pedal (00) or its confirmation (01).
export function parseParam(body) {
  if (!body || body.length !== 10 || body[0] !== 0x64 || body[1] !== 0x20 || body[2] > 1) return null;
  return {ack: body[2] === 1, slot: body[3], param: body[4], value: decodeValue(body[5], body[6])};
}

// Eight bytes on the wire carry seven: the first holds their top bits, bit 6 for the first.
export function unpack7bit(packed) {
  const data = [];
  for (let start = 0; start < packed.length; start += 8) {
    const high = packed[start];
    packed.slice(start + 1, start + 8).forEach((byte, index) => data.push(byte | ((high >> (6 - index)) & 1) << 7));
  }
  return Uint8Array.from(data);
}

const CRC_TABLE = Array.from({length: 256}, (_, n) => {
  for (let k = 0; k < 8; k++) n = n & 1 ? 0xEDB88320 ^ (n >>> 1) : n >>> 1;
  return n >>> 0;
});
// As the pedal sends it: binascii.crc32(data) ^ 0xFFFFFFFF.
export const pedalCrc = (data) => data.reduce((crc, b) => CRC_TABLE[(crc ^ b) & 0xFF] ^ (crc >>> 8), 0xFFFFFFFF) >>> 0;

// The knob values of an EDTB block (24 bytes, little-endian), from parameter 2 on.
function edtbValues(block) {
  const values = [];
  let bit = 30n;
  for (const width of EDTB_WIDTHS) {
    values.push(Number((block >> bit) & ((1n << BigInt(width)) - 1n)));
    bit += BigInt(width);
  }
  return values;
}

// The reply to query_patch, `64 12 01 <length LSB MSB> <patch, 7-bit packed> <CRC32, 5 bytes>` →
// {name, effects: [{id, enabled, firstParam, values}], data}; null if it is none or does not check out.
export function parsePatchDump(body) {
  if (!body || body.length < 10 || body[0] !== 0x64 || body[1] !== 0x12) return null;
  const length = decodeValue(body[3], body[4]);
  const data = unpack7bit(body.slice(5, -5));
  const crc = Array.from(body.slice(-5)).reduce((sum, b, index) => sum + b * 2 ** (7 * index), 0);
  if (data.length !== length || pedalCrc(data) !== crc) return null;
  if (ascii(data.slice(0, 4)) !== "PTCF" || length < 36) return null;
  const count = little(data, 12, 4);
  if (count > MAX_SLOTS || length < 36 + 4 * count) return null;
  const ids = Array.from({length: count}, (_, slot) => little(data, 36 + 4 * slot, 4));
  const settings = new Array(count).fill(null);
  let name = data.slice(26, 36);
  let position = 36 + 4 * count;
  while (position + 8 <= length) {
    const tag = ascii(data.slice(position, position + 4)), size = little(data, position + 4, 4);
    if (tag === "EDTB" && size >= 24 * count && position + 8 + 24 * count <= length) {
      for (let slot = 0; slot < count; slot++) {
        const start = position + 8 + 24 * slot;
        const block = data.slice(start, start + 24).reduceRight((sum, b) => sum << 8n | BigInt(b), 0n);
        if (Number((block >> 1n) & 0x1FFFFFFFn) === (ids[slot] & 0x1FFFFFFF)) {
          const values = edtbValues(block);
          settings[slot] = {enabled: Boolean(block & 1n), firstParam: values[0], values};
        }
      }
    } else if (tag === "NAME" && size > 0 && size <= length - position - 8) {
      name = data.slice(position + 8, position + 8 + size);
      name = name.slice(0, name.indexOf(0) < 0 ? name.length : name.indexOf(0));
      break;
    }
    position += 8 + size;
  }
  return {name: ascii(name).trimEnd(),
          effects: ids.map((id, slot) => ({id, ...(settings[slot] || {enabled: null, firstParam: null, values: []})})),
          data};
}

// --- The effects table (zoom_effects.json, effect_names.py) ---

const HIDDEN = new Set(["Dummy"]);
// {name, knobs} of an effect by its id; another category byte is fine if only one has its lower 24 bits.
export function effect(table, effectId) {
  if (!effectId) return null;
  const exact = table[effectId.toString(16).toUpperCase().padStart(8, "0")];
  if (exact) return exact;
  const same = Object.entries(table).filter(([key]) => (parseInt(key, 16) & 0xFFFFFF) === (effectId & 0xFFFFFF));
  return same.length === 1 ? same[0][1] : !same.length && (effectId & 0xFFFFFF) === 0xFF0 ? {name: "BPM", knobs: []} : null;
}
// [{param, name, max}] of an effect's knobs, param as sent to the pedal.
export const knobs = (table, effectId) => ((effect(table, effectId) || {}).knobs || [])
  .map(([name, max], index) => ({param: MIN_PARAM + index, name, max}))
  .filter((knob) => !HIDDEN.has(knob.name) && knob.max > 0);

// --- Ports ---

// The one port a name from the settings means: the one with exactly that name, else the one
// whose name contains it (case-insensitive). shortcut: no ALSA names as find_port knows them; enough for Chrome.
export function findPort(names, needle) {
  const lower = needle.toLowerCase(), sorted = [...new Set(names)].sort();
  for (const test of [(name) => name.toLowerCase() === lower, (name) => name.toLowerCase().includes(lower)]) {
    const found = sorted.filter(test);
    if (found.length === 1) return found[0];
    if (found.length > 1) throw new Error(`no unique port matching “${needle}” (found: ${found.join(", ")})`);
  }
  throw new Error(`no unique port matching “${needle}” (found: none) – ports there are: ${sorted.join(", ") || "none"}`);
}
