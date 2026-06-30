import pytest
from types import SimpleNamespace

from src.runtime import api_server


def test_api_server_parser_exposes_explicit_runtime_roles():
    args = api_server.build_parser().parse_args(["--role", "replica", "--port", "9000"])

    assert args.role == "replica"
    assert args.port == 9000
    assert args.live is False


@pytest.mark.parametrize("extra", [["--live"], ["--no-strategy"]])
def test_api_replica_rejects_execution_flags(extra):
    with pytest.raises(SystemExit):
        api_server.main(["--role", "replica", *extra])


def test_api_replica_starts_without_creating_execution_runner(monkeypatch):
    called = {}

    def fail_create_default_runner(**kwargs):
        raise AssertionError(f"unexpected execution runner: {kwargs}")

    def fake_uvicorn_run(app, *, host, port):
        called.update(app=app, host=host, port=port)

    monkeypatch.setattr(api_server, "create_default_runner", fail_create_default_runner)
    monkeypatch.setattr(api_server.uvicorn, "run", fake_uvicorn_run)

    api_server.main(["--role", "replica", "--host", "127.0.0.1", "--port", "9001"])

    assert called["host"] == "127.0.0.1"
    assert called["port"] == 9001
    assert called["app"].state.runtime_role == "replica"


def test_api_server_rejects_accidental_non_loopback_binding():
    with pytest.raises(SystemExit, match="--allow-remote"):
        api_server.main(["--role", "replica", "--host", "0.0.0.0"])


def test_api_server_requires_token_for_explicit_remote_binding(monkeypatch):
    monkeypatch.setattr(api_server, "settings", SimpleNamespace(MAYBECH_API_TOKEN=""))

    with pytest.raises(SystemExit, match="MAYBECH_API_TOKEN"):
        api_server.main(
            ["--role", "replica", "--host", "0.0.0.0", "--allow-remote"]
        )


def test_api_server_allows_authenticated_remote_replica(monkeypatch):
    called = {}
    monkeypatch.setattr(
        api_server,
        "settings",
        SimpleNamespace(MAYBECH_API_TOKEN="secret-token"),
    )
    monkeypatch.setattr(
        api_server.uvicorn,
        "run",
        lambda app, *, host, port: called.update(app=app, host=host, port=port),
    )

    api_server.main(
        ["--role", "replica", "--host", "0.0.0.0", "--allow-remote"]
    )

    assert called["host"] == "0.0.0.0"
    assert called["app"].state.authentication_required is True
