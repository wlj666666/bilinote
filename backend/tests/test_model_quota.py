"""Quota balances must distinguish unknown, zero, expiry and failed refreshes."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "isolated_model_quota", Path(__file__).resolve().parents[1] / "app/services/model_quota.py"
)
quota = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quota)


class ModelQuotaTests(unittest.TestCase):
    def setUp(self):
        quota._cache.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"BAILIAN_CONFIG_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = Path(self.temp.name) / "config.json"
        self.session = {"access_token": "private-console-token", "api_key": "private-inference-key"}
        self.config.write_text(json.dumps(self.session))
        self.provider = {"id": "qwen", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "private-inference-key"}

    def payload(self, remaining=1234, **extra):
        return {"data": {"DataV2": {"data": {"data": {"freeTierQuotas": [{
            "model": "example", "quotaTotal": remaining, "quotaInitTotal": 10000,
            "quotaValidityPeriod": 2000000000000, "quotaStatus": "VALID", **extra,
        }]}}}}}

    def response(self, payload):
        return io.BytesIO(json.dumps(payload).encode())

    def test_real_balance_expiry_and_cache_share_one_read_only_request(self):
        with patch.object(quota, "urlopen", return_value=self.response(self.payload())) as call:
            result = quota.quota_snapshot(self.provider, ["example", "missing"])
            cached = quota.quota_snapshot(self.provider, ["missing", "example"])
        self.assertEqual(result, cached)
        self.assertEqual(call.call_count, 1)
        row = result["models"][0]
        self.assertEqual((row["remaining_tokens"], row["total_tokens"], row["expires_at"]), (1234, 10000, 2000000000))
        self.assertEqual(row["status"], "available")
        missing = result["models"][1]
        self.assertIsNone(missing["remaining_tokens"])
        self.assertEqual(missing["status"], "unknown")
        request = call.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.host, "bailian-cs.console.aliyun.com")
        self.assertNotIn("private-console-token", json.dumps(result))

    def test_known_zero_is_exhausted_but_missing_and_nonfinite_are_unknown(self):
        for value, status in [(0, "exhausted"), (None, "unknown"), (float("nan"), "unknown"), (True, "unknown")]:
            with self.subTest(value=value):
                row = self.payload(value)["data"]["DataV2"]["data"]["data"]["freeTierQuotas"][0]
                normalized = quota._normalize(row, 100)
                self.assertEqual(normalized["status"], status)
                if status == "unknown":
                    self.assertIsNone(normalized["remaining_tokens"])

    def test_unused_expired_tokens_are_not_reported_as_available(self):
        row = self.payload(1000, quotaValidityPeriod=1000)["data"]["DataV2"]["data"]["data"]["freeTierQuotas"][0]
        self.assertEqual(quota._normalize(row, 100)["status"], "expired")

    def test_unauthenticated_and_malformed_config_never_contact_cloud(self):
        for data in ({}, [], {"active_config": "other"}):
            self.config.write_text(json.dumps(data))
            with patch.object(quota, "urlopen") as call:
                result = quota.quota_snapshot(self.provider, ["example"])
            self.assertEqual(result["status"], "needs_login")
            self.assertIsNone(result["models"][0]["remaining_tokens"])
            call.assert_not_called()

    def test_foreign_key_region_or_provider_does_not_use_console_session(self):
        for session in ({**self.session, "api_key": "other-key"}, {**self.session, "console_region": "ap-southeast-1"}):
            self.config.write_text(json.dumps(session))
            with patch.object(quota, "urlopen") as call:
                result = quota.quota_snapshot(self.provider, ["example"])
            self.assertEqual(result["status"], "needs_login")
            call.assert_not_called()
        with patch.object(quota, "urlopen") as call:
            result = quota.quota_snapshot({**self.provider, "base_url": "https://untrusted.example/v1"}, ["example"])
        self.assertEqual(result["status"], "unsupported")
        call.assert_not_called()

    def test_expired_login_and_network_failure_never_expose_credentials_or_stale_balance(self):
        with patch.object(quota, "urlopen", return_value=self.response({"data": {"errorCode": "NotLogined", "success": False}})):
            result = quota.quota_snapshot(self.provider, ["example"])
        self.assertEqual(result["status"], "needs_login")
        with patch.object(quota, "urlopen", side_effect=OSError("private-console-token")):
            result = quota.quota_snapshot(self.provider, ["example"])
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["updated_at"])
        self.assertIsNone(result["models"][0]["remaining_tokens"])
        self.assertNotIn("private-console-token", json.dumps(result))

    def test_direct_envelope_and_active_named_cli_profile(self):
        self.config.write_text(json.dumps({"active_config": "work", "work": self.session}))
        self.assertEqual(quota._read_session(), self.session)
        rows = [{"model": "example"}]
        self.assertEqual(quota._extract_quotas({"data": {"data": {"freeTierQuotas": rows}}}), rows)
        with self.assertRaises(ValueError):
            quota._extract_quotas({"data": {}})

    def test_expired_cache_failure_does_not_return_previous_balance(self):
        with patch.object(quota.time, "time", return_value=100), patch.object(quota, "urlopen", return_value=self.response(self.payload())):
            self.assertEqual(quota.quota_snapshot(self.provider, ["example"])["status"], "ok")
        with patch.object(quota.time, "time", return_value=161), patch.object(quota, "urlopen", side_effect=TimeoutError):
            result = quota.quota_snapshot(self.provider, ["example"])
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["models"][0]["remaining_tokens"])


if __name__ == "__main__":
    unittest.main()
