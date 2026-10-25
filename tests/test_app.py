"""Unit tests for app.py: target label/order helpers + HTTP routes.

Skipped if flask / python-dotenv are not installed. pjsua2 is stubbed by
conftest; sip_client is replaced with a fake so no real SIP stack is touched.
"""
import os
import pytest

pytest.importorskip("flask")
pytest.importorskip("dotenv")

# Config must exist before importing app (module reads env at import time).
os.environ.setdefault("SIP_PROXY", "sips.peoplefone.lt:5060")
os.environ.setdefault("SIP_USERNAME", "90852528635")
os.environ.setdefault("SIP_PASSWORD", "secret")
os.environ["PHONE_NUMBER_DOOR"] = "+37061454944"
os.environ["PHONE_NUMBER_GATE"] = "+37068245573"
os.environ["PHONE_NUMBER_LAB"] = "+37060029810"

import app as app_module  # noqa: E402


class FakeSip:
    def __init__(self):
        self.calls = []

    def get_status(self):
        return {"registered": True, "has_active_call": False,
                "proxy": "sips.peoplefone.lt:5060", "username": "90852528635"}

    def render_metrics(self):
        return b"gate_sip_registered 1\n"

    def make_call(self, number, target="?", source=None):
        self.calls.append((target, number))
        return {"success": True, "message": "Call initiated."}


@pytest.fixture()
def client():
    app_module.sip_client = FakeSip()      # skip lazy init in before_request
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


# ---------------------------------------------------------------- helpers
class TestTargetMeta:
    def test_default_labels(self):
        assert app_module._target_label("GATE") == "Parking Gate"
        assert app_module._target_label("DOOR") == "Main Entrance"
        assert app_module._target_label("LAB") == "Lab"

    def test_label_env_override(self, monkeypatch):
        monkeypatch.setenv("PHONE_LABEL_LAB", "Workshop")
        assert app_module._target_label("LAB") == "Workshop"

    def test_unknown_label_titlecases(self):
        assert app_module._target_label("GARAGE") == "Garage"

    def test_default_order(self):
        assert app_module._target_order("GATE") == 1
        assert app_module._target_order("DOOR") == 2
        assert app_module._target_order("LAB") == 3

    def test_order_env_override_and_bad_value(self, monkeypatch):
        monkeypatch.setenv("PHONE_ORDER_GATE", "5")
        assert app_module._target_order("GATE") == 5
        monkeypatch.setenv("PHONE_ORDER_GATE", "notanint")
        assert app_module._target_order("GATE") == 99

    def test_ordered_targets_sequence(self):
        ids = [t["id"] for t in app_module.ordered_targets()]
        assert ids == ["GATE", "DOOR", "LAB"]
        first = app_module.ordered_targets()[0]
        assert first == {"id": "GATE", "label": "Parking Gate"}


# ---------------------------------------------------------------- routes
class TestRoutes:
    def test_targets_ordered_objects(self, client):
        data = client.get("/targets").get_json()
        assert data["targets"][0] == {"id": "GATE", "label": "Parking Gate"}
        assert [t["id"] for t in data["targets"]] == ["GATE", "DOOR", "LAB"]

    def test_status(self, client):
        data = client.get("/status").get_json()
        assert data["sip_client_initialized"] is True
        assert data["registered"] is True

    def test_metrics_endpoint(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert b"gate_sip_registered 1" in resp.data

    def test_call_unknown_target_400(self, client):
        resp = client.post("/call", json={"target": "NOPE"})
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_call_missing_target_400(self, client):
        resp = client.post("/call", json={})
        assert resp.status_code == 400

    def test_call_known_target_dials_number(self, client):
        resp = client.post("/call", json={"target": "GATE"})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        # the correct E.164 number was passed with the target label
        assert ("GATE", "+37068245573") in app_module.sip_client.calls
