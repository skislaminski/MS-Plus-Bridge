"""mido's ports over Web MIDI, for the bridge in a phone's browser (webmidi/browser.py). The page
(webmidi/index.html) holds the MIDIAccess as window.webmidi and keeps what an input receives
until it is fetched here, once per pass of the bridge's loop."""
import js
from mido.ports import BaseInput, BaseOutput
from pyodide.ffi import JsException, to_js


def _midi(function, *args):
    """A port that is gone raises OSError, as with rtmidi: the bridge then connects anew."""
    try:
        return function(*args)
    except JsException as error:
        raise OSError(str(error)) from None


def get_devices(**kwargs) -> list:
    return [{"name": name, "is_input": is_input, "is_output": not is_input}
            for name, is_input in _midi(js.webmidi.ports).to_py()]


class Input(BaseInput):
    def _open(self, **kwargs) -> None:
        _midi(js.webmidi.openInput, self.name)

    def _receive(self, block=True) -> None:
        for data in _midi(js.webmidi.take, self.name).to_py():
            self._parser.feed(data)

    def _close(self) -> None:
        _midi(js.webmidi.closeInput, self.name)


class Output(BaseOutput):
    def _open(self, **kwargs) -> None:
        _midi(js.webmidi.openOutput, self.name)

    def _send(self, message) -> None:
        _midi(js.webmidi.send, self.name, to_js(message.bytes()))
