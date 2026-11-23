// The pedal over Web MIDI, as bridge.Session talks to it: identity, edit mode, the current preset
// and its patch, and one knob set within the range its effect file gives (Session.edit_knob).
// Every SysEx leaves through send(), where the allowlist and the approvals apply (ZoomSender.send).
import * as zs from "./zoom_sysex.js";

export const PORT_NAME = "ZOOM MS Plus Series";     // config.example.yaml's default
const ACK_MS = 250;                                 // bridge.ACK_TIMEOUT

export class Pedal {
  // access: a MIDIAccess; approvals: {backupConfirmed, isApproved(kind)}; table: zoom_effects.json;
  // onChange(): something the page shows has changed; log(text): what was sent and received.
  constructor(access, approvals, table, {onChange = () => {}, log = () => {}, portName = PORT_NAME} = {}) {
    Object.assign(this, {access, approvals, table, onChange, log});
    const names = (ports) => [...ports.values()].map((port) => port.name);
    const port = (ports) => [...ports.values()].find((p) => p.name === zs.findPort(names(ports), portName));
    this.input = port(access.inputs);
    this.output = port(access.outputs);
    this.deviceId = null;
    this.identity = null;
    this.preset = null;          // {bank, program}
    this.patch = null;           // parsePatchDump
    this.targets = [];           // what set_param may reach: every known knob of the chain, its whole range
    this.waiters = new Set();
    this.bank = 0;
    this.bankSelected = false;
    this.input.onmidimessage = (event) => this.receive(event.data);
  }

  send(msg) {
    const kind = zs.classify(msg, this.deviceId, this.targets);
    if (!this.approvals.backupConfirmed) throw new zs.NotApproved("backup");
    if (!this.approvals.isApproved(kind)) throw new zs.NotApproved(kind);
    this.log(`TX  ${zs.toHex(msg)}  (${kind})`);
    this.output.send(Array.from(msg));
    return kind;
  }

  // The first incoming SysEx for which match(raw) is truthy, its result; null after ms.
  wait(match, ms) {
    return new Promise((resolve) => {
      const waiter = (raw) => {
        const result = match(raw);
        if (result) { this.waiters.delete(waiter); clearTimeout(timer); resolve(result); }
      };
      const timer = setTimeout(() => { this.waiters.delete(waiter); resolve(null); }, ms);
      this.waiters.add(waiter);
    });
  }

  receive(data) {
    const raw = Uint8Array.from(data);
    this.log(`RX  ${zs.toHex(raw)}`);
    if ((raw[0] & 0xF0) === 0xB0 && raw[1] === 32) {
      this.bank = raw[2];
      this.bankSelected = true;
    } else if ((raw[0] & 0xF0) === 0xC0) {
      // a preset comes with bank select; the bursts of "program change 0" while a name is edited do not
      if (this.bankSelected) this.setPreset(this.bank, raw[1]);
      this.bankSelected = false;
    } else if (raw[0] === zs.SOX && this.deviceId != null) {
      const body = zs.zoomBody(raw, this.deviceId);
      const dump = zs.parsePatchDump(body), change = zs.parseParam(body);
      if (dump) this.setPatch(dump);
      else if (change && !change.ack && this.patch && change.slot < this.patch.effects.length && change.param >= zs.MIN_PARAM) {
        this.patch.effects[change.slot].values[change.param - zs.MIN_PARAM] = change.value;   // turned on the pedal
        this.onChange();
      }
    }
    for (const waiter of [...this.waiters]) waiter(raw);
  }

  setPreset(bank, program) {
    const fresh = this.preset && (this.preset.bank !== bank || this.preset.program !== program);
    this.preset = {bank, program};
    this.onChange();
    if (!fresh) return;
    try {
      this.send(zs.buildQueryPatch(this.deviceId));     // another preset on the pedal: its effects
    } catch (error) {                                   // not approved: no knobs of the old chain to set
      this.patch = null;
      this.targets = [];
      this.log(`not sent: ${error.message}`);
      this.onChange();
    }
  }

  setPatch(dump) {
    this.patch = dump;
    this.targets = dump.effects.flatMap((effect, slot) =>
      zs.knobs(this.table, effect.id).map((knob) => ({slot, param: knob.param, min: 0, max: knob.max})));
    this.onChange();
  }

  async start() {
    this.send(zs.buildIdentityRequest());
    const identity = await this.wait(zs.parseIdentityReply, 2000);
    if (!identity) throw new Error("no identity reply from the pedal");
    this.identity = identity;
    this.deviceId = identity.deviceId;
    this.send(zs.buildEditEnable(this.deviceId));
    if (!await this.wait((raw) => zs.isAck(zs.zoomBody(raw, this.deviceId)), 1000)) throw new Error("no ack for edit enable");
    this.send(zs.buildQueryProgram(this.deviceId));
    await this.wait(() => this.preset, 500);
    this.send(zs.buildQueryPatch(this.deviceId));
    await this.wait(() => this.patch, 1000);
    if (!this.patch) throw new Error("the pedal has not reported its patch");
  }

  // A knob of an effect in the chain to a value. Resolves whether the pedal confirmed it (it
  // confirms only a change: a value it holds already draws no ack).
  async setKnob(slot, param, value) {
    const effect = this.patch && this.patch.effects[slot];
    const knob = effect && zs.knobs(this.table, effect.id).find((k) => k.param === param);
    if (!knob) throw new Error(`effect ${slot + 1} has no knob ${param - 1} that is known`);
    if (!(value >= 0 && value <= knob.max)) throw new Error(`${knob.name} goes from 0 to ${knob.max}`);
    this.send(zs.buildSetParam(this.deviceId, slot, param, value));
    const ack = await this.wait((raw) => {
      const change = zs.parseParam(zs.zoomBody(raw, this.deviceId));
      return change && change.ack && change.slot === slot && change.param === param && change.value === value;
    }, ACK_MS);
    if (ack) effect.values[param - zs.MIN_PARAM] = value;
    return Boolean(ack);
  }

  async close() {
    try {
      if (this.deviceId != null) {
        this.send(zs.buildEditDisable(this.deviceId));
        await this.wait((raw) => zs.isAck(zs.zoomBody(raw, this.deviceId)), 500);
      }
    } finally {
      this.input.onmidimessage = null;
    }
  }
}
