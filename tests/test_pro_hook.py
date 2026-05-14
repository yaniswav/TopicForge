"""Tests for `topicforge.server.app._try_register_pro`.

The OSS core ships a small hook in `server/app.py` that attempts to
import an optional `topicforge_pro` plugin package at startup and call
its `register(mcp)` entrypoint. This test pins the contract of that
hook without depending on a real Pro package install:

* When `topicforge_pro` is not importable → return False, no exception.
* When `topicforge_pro` is importable AND exposes a `register(mcp)`
  callable → call it and return True.
* When `topicforge_pro.register` raises → log and return False (the
  OSS surface keeps working).

We use `monkeypatch.setitem(sys.modules, ...)` to inject a fake
package rather than mocking the import — same convention as
`tests/test_health.py` for `importlib.util.find_spec` patches.
"""

from __future__ import annotations

import sys
import types

import pytest

from topicforge.server.app import _try_register_pro


def _make_fake_pro_package(register_fn: object) -> types.ModuleType:
    """Build a stand-in `topicforge_pro` module exposing `register`."""
    module = types.ModuleType("topicforge_pro")
    module.register = register_fn  # type: ignore[attr-defined]
    return module


def test_returns_false_when_pro_package_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `topicforge_pro` installed → the OSS server boots without Pro."""
    monkeypatch.delitem(sys.modules, "topicforge_pro", raising=False)

    # Stub the module finder so an actual import attempt fails fast.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "topicforge_pro":
            raise ImportError("simulated: topicforge_pro not installed")
        return real_import(name, *args, **kwargs)  # type: ignore[operator]

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert _try_register_pro(mcp=object()) is False


def test_returns_true_when_pro_registers_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """`topicforge_pro.register(mcp)` is called and we return True."""
    calls: list[object] = []

    def fake_register(mcp: object) -> None:
        calls.append(mcp)

    fake_module = _make_fake_pro_package(fake_register)
    monkeypatch.setitem(sys.modules, "topicforge_pro", fake_module)

    sentinel = object()
    assert _try_register_pro(mcp=sentinel) is True
    assert calls == [sentinel]


def test_returns_false_when_register_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failing Pro plugin must NOT break the free MVP."""

    def boom(_mcp: object) -> None:
        raise RuntimeError("simulated Pro plugin crash")

    fake_module = _make_fake_pro_package(boom)
    monkeypatch.setitem(sys.modules, "topicforge_pro", fake_module)

    with caplog.at_level("ERROR"):
        result = _try_register_pro(mcp=object())

    assert result is False
    assert any("topicforge-pro registration failed" in record.message for record in caplog.records)
