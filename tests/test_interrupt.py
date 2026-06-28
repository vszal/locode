from prompt_toolkit.input import create_pipe_input

from locode.agent.cancel import CancelToken
from locode.ui.interrupt import RawWriter, make_key_handler


def test_esc_triggers_cancel():
    cancel = CancelToken()
    with create_pipe_input() as inp:
        handler = make_key_handler(inp, cancel)
        inp.send_text("\x1b")  # lone ESC — the case that was silently buffered
        handler()
    assert cancel.cancelled


def test_ctrl_c_triggers_cancel():
    cancel = CancelToken()
    with create_pipe_input() as inp:
        handler = make_key_handler(inp, cancel)
        inp.send_text("\x03")  # Ctrl-C
        handler()
    assert cancel.cancelled


def test_ordinary_key_does_not_cancel():
    cancel = CancelToken()
    with create_pipe_input() as inp:
        handler = make_key_handler(inp, cancel)
        inp.send_text("a")
        handler()
    assert not cancel.cancelled


def test_raw_writer_translates_newlines_only_in_raw_mode(capsys):
    w = RawWriter()
    w.write("a\nb")
    w.raw = True
    w.write("\nc")
    out = capsys.readouterr().out
    assert out == "a\nb\r\nc"
