// A MIDIAccess that reaches the simulated MS-60B+ of tests/sim.py through webmidi/sim_server.py
// instead of a MIDI port: what is sent goes to it in order, what it answers is fetched every few ms.
// For the page loaded with ?sim and for tests/test_webmidi.py; it never touches Web MIDI itself.
export function simulatedAccess(base = "", name = "ZOOM MS Plus Series") {
  let sending = Promise.resolve(), stopped = false;
  const input = {name, type: "input", onmidimessage: null};
  const output = {name, type: "output", send(data) {
    const body = JSON.stringify(Array.from(data));
    sending = sending.then(() => fetch(`${base}/sim/send`, {method: "POST", body}));
  }};
  (async function poll() {
    while (!stopped) {
      await sending;
      const messages = await (await fetch(`${base}/sim/poll`)).json();
      for (const data of messages) input.onmidimessage?.({data: Uint8Array.from(data)});
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  })();
  return {inputs: new Map([["sim-in", input]]), outputs: new Map([["sim-out", output]]), sysexEnabled: true,
          stop: () => { stopped = true; return sending; }};
}
