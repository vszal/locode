"""The server-log reader behind the silent-server watchdog.

Offset-based on purpose: a traceback carries no timestamp of its own, so "what
was appended since I sent my request" is the only precise way to attribute a
failure to this request rather than to a previous one.
"""

from locode.server import logs


REAL = ("2026-08-02 11:24:56,162 - INFO - HTTP Request: GET ...\n"
        "Exception in thread Thread-1 (_generate):\n"
        "Traceback (most recent call last):\n"
        "  File \"/opt/homebrew/lib/python3.11/site-packages/mlx_lm/utils.py\","
        " line 189, in _get_classes\n"
        "    arch = importlib.import_module(f\"mlx_lm.models.{model_type}\")\n"
        "ModuleNotFoundError: No module named 'mlx_lm.models.gemma4_unified'\n"
        "\nDuring handling of the above exception, another exception occurred:\n"
        "\nTraceback (most recent call last):\n"
        "ValueError: Model type gemma4_unified not supported.\n")


def test_mark_then_fatal_since_finds_the_real_cause(tmp_path):
    log = tmp_path / "mlx-server.log"
    log.write_text("older lines that are not this request's problem\n")
    at = logs.mark(log)
    log.write_text(log.read_text() + REAL)
    assert logs.fatal_since(at, log) == (
        "ValueError: Model type gemma4_unified not supported.")


def test_an_error_logged_BEFORE_the_request_is_not_attributed(tmp_path):
    """The previous run's failure must not be blamed on this request."""
    log = tmp_path / "mlx-server.log"
    log.write_text(REAL)
    at = logs.mark(log)
    log.write_text(log.read_text() + "2026-08-02 - INFO - all is well\n")
    assert logs.fatal_since(at, log) == ""


def test_healthy_output_yields_nothing(tmp_path):
    log = tmp_path / "mlx-server.log"
    at = logs.mark(log)
    log.write_text("INFO - Starting httpd at 127.0.0.1 on port 8081...\n"
                   "INFO - Prompt processing progress: 10240/10658\n")
    assert logs.fatal_since(at, log) == ""


def test_nothing_appended_yields_nothing(tmp_path):
    log = tmp_path / "mlx-server.log"
    log.write_text(REAL)
    assert logs.fatal_since(logs.mark(log), log) == ""


def test_a_missing_log_is_survivable(tmp_path):
    missing = tmp_path / "nope.log"
    assert logs.mark(missing) == 0
    assert logs.fatal_since(0, missing) == ""


def test_a_restarted_server_truncating_the_log_still_reports(tmp_path):
    """Offset past EOF means the file was rotated/truncated, not that all is well."""
    log = tmp_path / "mlx-server.log"
    log.write_text("x" * 5000)
    at = logs.mark(log)
    log.write_text(REAL)                      # shorter than `at`
    assert logs.fatal_since(at, log) == ""    # size <= offset: nothing new to blame


def test_a_traceback_without_a_recognisable_last_line_is_not_invented(tmp_path):
    log = tmp_path / "mlx-server.log"
    at = logs.mark(log)
    log.write_text("Traceback (most recent call last):\n  File x, line 1\n")
    assert logs.fatal_since(at, log) == ""
