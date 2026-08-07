"""Single-instance namespace compatibility and smoke-test isolation."""
from __future__ import annotations

import sys
from uuid import uuid4

import pytest

from src.config.paths import settings_path
from src.service.single_instance import SingleInstanceGuard


_NAMESPACE_ENV = "RIME_CONFIG_INSTANCE_NAMESPACE"
_DEFAULT_MUTEX = r"Local\RimeConfigTool.SingleInstance"
_DEFAULT_WAKE_EVENT = r"Local\RimeConfigTool.ShowMainWindow"


def test_default_namespace_keeps_existing_kernel_names(monkeypatch) -> None:
    monkeypatch.delenv(_NAMESPACE_ENV, raising=False)

    guard = SingleInstanceGuard()

    assert guard._name == _DEFAULT_MUTEX
    assert guard._wake_event == _DEFAULT_WAKE_EVENT


def test_explicit_namespace_uses_stable_isolated_kernel_names(monkeypatch) -> None:
    namespace = f"pytest-{uuid4()}"
    monkeypatch.setenv(_NAMESPACE_ENV, namespace)

    first = SingleInstanceGuard()
    second = SingleInstanceGuard()

    assert first._name == second._name
    assert first._wake_event == second._wake_event
    assert first._name.startswith(r"Local\RimeConfigTool.Test.")
    assert first._wake_event.startswith(r"Local\RimeConfigTool.Test.")
    assert namespace not in first._name
    assert namespace not in first._wake_event


def test_default_and_explicit_namespaces_do_not_conflict(monkeypatch) -> None:
    monkeypatch.delenv(_NAMESPACE_ENV, raising=False)
    default_guard = SingleInstanceGuard()
    monkeypatch.setenv(_NAMESPACE_ENV, f"pytest-{uuid4()}")
    isolated_guard = SingleInstanceGuard()

    assert default_guard._name != isolated_guard._name
    assert default_guard._wake_event != isolated_guard._wake_event


def test_namespace_override_does_not_change_settings_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv(_NAMESPACE_ENV, raising=False)
    default_path = settings_path()

    monkeypatch.setenv(_NAMESPACE_ENV, f"pytest-{uuid4()}")

    assert settings_path() == default_path


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows kernel objects")
def test_distinct_test_namespaces_can_run_side_by_side(monkeypatch) -> None:
    monkeypatch.setenv(_NAMESPACE_ENV, f"pytest-a-{uuid4()}")
    first = SingleInstanceGuard()
    duplicate = SingleInstanceGuard()
    monkeypatch.setenv(_NAMESPACE_ENV, f"pytest-b-{uuid4()}")
    second = SingleInstanceGuard()

    try:
        assert first.acquire()
        assert not duplicate.acquire()
        assert second.acquire()
    finally:
        duplicate.release()
        second.release()
        first.release()
