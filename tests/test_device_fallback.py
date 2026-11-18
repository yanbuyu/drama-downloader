"""Regression tests for opportunistic device reuse and recovery."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from drama_direct import DevicePool, DirectClient, DirectError


class FakeResponse:
    def __init__(self, code=200, payload=None):
        self.status_code = code
        self.payload = payload if payload is not None else {"code": 0, "data": {"ok": True}}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def make_local(n):
    return {
        "device_id": str(111000000000000000 + n),
        "install_id": str(222000000000000000 + n),
        "cdid": f"local-{n}",
        "openudid": f"local-{n}",
        "origin": "local_generated",
        "server_verified": False,
        "status": "active",
    }


class DeviceReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.filename = str(Path(self.temp.name) / "devices.json")
        self.url = "https://example.test/reading/?device_id={device_id}&iid={install_id}&aid=1967"

    def client(self):
        return DirectClient(DevicePool(self.filename), timeout=(1, 1))

    def test_reuse_saved_device_without_registering(self):
        Path(self.filename).write_text(json.dumps({"android": [
            {"device_id": "101", "install_id": "202", "status": "active"}
        ]}))
        client = self.client()
        with patch("drama_direct.register_device") as reg, \
                patch("drama_direct.requests.request", return_value=FakeResponse()) as request:
            result = client.request(self.url)
        self.assertTrue(result["data"]["ok"])
        reg.assert_not_called()
        self.assertIn("device_id=101", request.call_args.args[1])
        self.assertTrue(client.pool.status()[0]["server_verified"])

    def test_empty_pool_generates_candidate_without_registering(self):
        client = self.client()
        with patch("drama_direct.generate_local_device", return_value=make_local(1)), \
                patch("drama_direct.register_device") as reg, \
                patch("drama_direct.requests.request", return_value=FakeResponse()):
            client.request(self.url)
        reg.assert_not_called()
        self.assertEqual(client.pool.status()[0]["origin"], "local_generated")
        self.assertTrue(client.pool.status()[0]["server_verified"])

    def test_auth_rejection_registers_only_when_no_cached_device_remains(self):
        client = self.client()
        registered = {"device_id": "303", "install_id": "404", "status": "active"}
        with patch("drama_direct.generate_local_device", return_value=make_local(1)), \
                patch("drama_direct.register_device", return_value=registered) as reg, \
                patch("drama_direct.requests.request", side_effect=[
                    FakeResponse(403), FakeResponse()
                ]):
            client.request(self.url)
        reg.assert_called_once()
        devices = client.pool.status()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["device_id"], "303")
        self.assertEqual(devices[0]["origin"], "server_registered")
        self.assertTrue(devices[0]["server_verified"])

    def test_other_cached_device_is_used_before_registration(self):
        Path(self.filename).write_text(json.dumps({"android": [
            {"device_id": "101", "install_id": "201", "status": "active"},
            {"device_id": "102", "install_id": "202", "status": "active"},
        ]}))
        client = self.client()
        with patch("drama_direct.register_device") as reg, \
                patch("drama_direct.requests.request", side_effect=[
                    FakeResponse(401), FakeResponse()
                ]):
            client.request(self.url)
        reg.assert_not_called()
        self.assertEqual([d["device_id"] for d in client.pool.status()], ["102"])
        self.assertTrue(client.pool.status()[0]["server_verified"])

    def test_failed_registration_still_tries_new_local_candidate(self):
        client = self.client()
        candidates = [make_local(1), make_local(2)]
        with patch("drama_direct.generate_local_device", side_effect=candidates) as local, \
                patch("drama_direct.register_device", side_effect=DirectError("network error")) as reg, \
                patch("drama_direct.requests.request", side_effect=[
                    FakeResponse(403), FakeResponse()
                ]):
            client.request(self.url)
        reg.assert_called_once()
        self.assertEqual(local.call_count, 2)
        self.assertEqual(client.pool.status()[0]["device_id"], make_local(2)["device_id"])
        self.assertTrue(client.pool.status()[0]["server_verified"])

    def test_business_auth_code_also_triggers_recovery(self):
        client = self.client()
        with patch("drama_direct.generate_local_device", return_value=make_local(1)), \
                patch("drama_direct.register_device", return_value={
                    "device_id": "303", "install_id": "404", "status": "active"
                }) as reg, \
                patch("drama_direct.requests.request", side_effect=[
                    FakeResponse(200, {"code": 403, "message": "device invalid"}),
                    FakeResponse()
                ]):
            client.request(self.url)
        reg.assert_called_once()


if __name__ == "__main__":
    unittest.main()
