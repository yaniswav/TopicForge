"""Serving branch of `topicforge.__main__.main`, with every collaborator faked."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from topicforge import __main__ as entry
from topicforge.server.http import TOKEN_ENV


class _FakeApp:
    """Stand-in MCP app whose `run` can raise on demand."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.ran = False

    def run(self) -> None:
        self.ran = True
        if self.error is not None:
            raise self.error


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Replace settings, app builder and HTTP server in the entrypoint namespace."""
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    state = SimpleNamespace(app=_FakeApp(), http_calls=[], http_error=None, built=[])
    settings = SimpleNamespace(log_level="INFO", mode="mock")

    def fake_build(received: object) -> _FakeApp:
        state.built.append(received)
        return state.app

    def fake_serve(*args: object) -> None:
        state.http_calls.append(args)
        if state.http_error is not None:
            raise state.http_error

    state.settings = settings
    monkeypatch.setattr(entry, "load_settings", lambda: settings)
    monkeypatch.setattr(entry, "build_app", fake_build)
    monkeypatch.setattr(entry, "serve_http", fake_serve)
    return state


def test_a_configuration_error_prints_to_stderr_and_returns_2(
    fakes: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> object:
        raise ValueError("bad mode")

    monkeypatch.setattr(entry, "load_settings", broken)
    assert entry.main([]) == 2
    assert "configuration error: bad mode" in capsys.readouterr().err
    assert fakes.built == []


def test_stdio_builds_the_app_runs_it_and_returns_0(fakes: SimpleNamespace) -> None:
    assert entry.main([]) == 0
    assert fakes.built == [fakes.settings]
    assert fakes.app.ran is True
    assert fakes.http_calls == []


def test_http_transport_calls_serve_http_with_defaults(fakes: SimpleNamespace) -> None:
    assert entry.main(["--transport", "streamable-http"]) == 0
    assert fakes.http_calls == [(fakes.app, entry.DEFAULT_HTTP_PORT, entry.LOOPBACK_HOST, None)]
    assert fakes.app.ran is False


def test_http_transport_passes_port_host_and_token(
    fakes: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "t" * 40
    monkeypatch.setenv(TOKEN_ENV, token)
    argv = ["--transport", "streamable-http", "--port", "9123", "--host", " 0.0.0.0 "]
    assert entry.main(argv) == 0
    assert fakes.http_calls == [(fakes.app, 9123, "0.0.0.0", token)]


def test_keyboard_interrupt_on_stdio_returns_0(fakes: SimpleNamespace) -> None:
    fakes.app.error = KeyboardInterrupt()
    assert entry.main([]) == 0


def test_keyboard_interrupt_on_http_returns_0(fakes: SimpleNamespace) -> None:
    fakes.http_error = KeyboardInterrupt()
    assert entry.main(["--transport", "streamable-http"]) == 0


def test_a_crash_while_serving_is_logged_and_returns_1(
    fakes: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    fakes.app.error = RuntimeError("boom")
    with caplog.at_level(logging.ERROR, logger="topicforge"):
        assert entry.main([]) == 1
    assert any("crashed while serving" in r.getMessage() for r in caplog.records)


def test_an_http_crash_returns_1(fakes: SimpleNamespace) -> None:
    fakes.http_error = OSError("address in use")
    assert entry.main(["--transport", "streamable-http"]) == 1
