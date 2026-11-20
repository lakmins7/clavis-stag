"""Exercise STAG through the API used by Clavis's shared CLI, without hardware."""

from fastapi.testclient import TestClient

from clavis.api.app import create_app
from clavis_stag.controls import ChannelState
from clavis_stag.plugin import plugin
from clavis_stag.registers import ASC_SELECT, ASC_START, ASC_TESTPULSE


def test_shared_clavis_api_initialization_and_incremental_edits(monkeypatch, client):
    monkeypatch.setattr("clavis.core.service.load_entry_point_plugins", lambda: [plugin()])
    monkeypatch.setattr("clavis_stag.plugin.RbcpRegisterClient", lambda *args, **kwargs: client)
    with TestClient(create_app()) as api:
        types = api.get("/api/board-types")
        assert types.status_code == 200
        assert [entry["board_type"] for entry in types.json()] == ["stag"]
        created = api.post("/api/boards", json={
            "board_id": "stag-001", "board_type": "stag", "name": "STAG", "host": "192.0.2.1",
        })
        assert created.status_code == 200
        assert client.writes == []
        base = "/api/boards/stag-001"
        connected = api.post(f"{base}/connect")
        assert connected.status_code == 200 and connected.json()["connected"]
        for chip in range(4):
            initialized = api.post(f"{base}/groups/{chip}", json={"threshold": 1.8})
            assert initialized.status_code == 200
        assert [value for address, value in client.writes if address == ASC_SELECT] == [1, 0, 2, 0, 4, 0, 8, 0]
        assert sum(address == ASC_START for address, _ in client.writes) == 320
        status = api.get(f"{base}/status").json()
        assert status["channel_options"] == {str(i): ChannelState().as_options() for i in range(1, 65)}

        client.writes.clear()
        for channel in ("1", "2", "17"):
            result = api.post(f"{base}/channels/{channel}", json={"threshold": 0.02, "testpulse": False})
            assert result.status_code == 200
        assert [value for address, value in client.writes if address == ASC_SELECT] == [1, 0, 1, 0, 2, 0]
        status = result.json()
        assert all(status["channel_options"][channel]["threshold"] == 0.02 for channel in ("1", "2", "17"))
        assert status["channel_options"]["64"] == ChannelState().as_options()
        pulse = api.post(f"{base}/global", json={"testpulse_mask": 0})
        assert pulse.status_code == 200
        assert client.writes[-1] == (ASC_TESTPULSE, 0)
        before = client.writes.copy()
        disconnected = api.post(f"{base}/disconnect")
        assert disconnected.status_code == 200 and not disconnected.json()["connected"]
        assert client.closed and client.writes == before
