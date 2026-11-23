import logging
from pathlib import Path

import pytest
import requests

from netboxsync.netbox import NetBoxError, PynetboxReader
from netboxsync.tls import ca_file_from_env, warn_if_overridden


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ["REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "DIODE_CERT_FILE", "NETBOX_CA_FILE"]:
        monkeypatch.delenv(var, raising=False)


def test_requests_env_bundle_beats_session_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Why D14 exists: the env bundle replaces the CA file the Diode SDK sets."""
    session = requests.Session()
    session.verify = "/etc/diode-ca.pem"
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/etc/other.pem")
    settings = session.merge_environment_settings("https://x", {}, None, None, None)
    assert settings["verify"] == "/etc/other.pem"


def test_ca_file_expands_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "ca.pem").write_text("x")
    monkeypatch.setenv("DIODE_CERT_FILE", "~/ca.pem")
    assert ca_file_from_env("DIODE_CERT_FILE") == tmp_path / "ca.pem"


def test_missing_ca_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIODE_CERT_FILE", "/nonexistent/ca.pem")
    with pytest.raises(FileNotFoundError, match="DIODE_CERT_FILE"):
        ca_file_from_env("DIODE_CERT_FILE")


def test_warns_only_when_both_are_set(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/etc/other.pem")
    with caplog.at_level(logging.WARNING):
        warn_if_overridden("DIODE_CERT_FILE")
    assert not caplog.records

    monkeypatch.setenv("DIODE_CERT_FILE", "/etc/diode-ca.pem")
    with caplog.at_level(logging.WARNING):
        warn_if_overridden("DIODE_CERT_FILE")
    assert "REQUESTS_CA_BUNDLE is set" in caplog.text


def test_netbox_session_gets_its_ca_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ca.pem").write_text("x")
    monkeypatch.setenv("NETBOX_URL", "https://netbox.example.net")
    monkeypatch.setenv("NETBOX_TOKEN", "t")
    monkeypatch.setenv("NETBOX_CA_FILE", str(tmp_path / "ca.pem"))
    reader = PynetboxReader.from_env()
    assert reader._api.http_session.verify == str(tmp_path / "ca.pem")

    monkeypatch.setenv("NETBOX_CA_FILE", "/nonexistent/ca.pem")
    with pytest.raises(NetBoxError, match="no such file"):
        PynetboxReader.from_env()
