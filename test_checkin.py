"""Cookie 兼容逻辑的回归测试。"""
import sys
import types
import unittest
from typing import cast
from unittest import mock


# 开发机未安装 requests 时也能测试纯 Cookie 逻辑；GitHub Actions 中会使用
# 工作流安装的真实 requests 包。
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")

    class RequestException(Exception):
        pass

    class HTTPError(RequestException):
        pass

    class JSONDecodeError(RequestException, ValueError):
        pass

    setattr(requests_stub, "Response", type("Response", (), {}))
    setattr(requests_stub, "Session", type("Session", (), {}))
    setattr(requests_stub, "exceptions", types.SimpleNamespace(
        RequestException=RequestException,
        HTTPError=HTTPError,
        JSONDecodeError=JSONDecodeError,
    ))
    setattr(requests_stub, "post", lambda *_: None)
    sys.modules["requests"] = requests_stub

import checkin


class CookieCompatibilityTests(unittest.TestCase):
    def test_accepts_legacy_koa_pair(self):
        valid, reason = checkin.validate_cookie("koa:sess=legacy; koa:sess.sig=legacy-signature")
        self.assertTrue(valid, reason)
        self.assertEqual(["koa"], checkin.detect_session_prefixes(
            "koa:sess=legacy; koa:sess.sig=legacy-signature"
        ))

    def test_accepts_new_gld_pair(self):
        valid, reason = checkin.validate_cookie("gld:sess=current; gld:sess.sig=current-signature")
        self.assertTrue(valid, reason)
        self.assertEqual(["gld"], checkin.detect_session_prefixes(
            "gld:sess=current; gld:sess.sig=current-signature"
        ))

    def test_accepts_transition_cookie_with_unrelated_fields(self):
        cookie = (
            "__stripe_mid=unrelated; "
            "koa:sess=legacy; koa:sess.sig=legacy-signature; "
            "gld:sess=current; gld:sess.sig=current-signature"
        )
        valid, reason = checkin.validate_cookie(cookie)
        self.assertTrue(valid, reason)
        self.assertEqual(["gld", "koa"], checkin.detect_session_prefixes(cookie))

    def test_rejects_incomplete_new_pair_even_if_legacy_pair_exists(self):
        cookie = "koa:sess=legacy; koa:sess.sig=legacy-signature; gld:sess=current"
        valid, reason = checkin.validate_cookie(cookie)
        self.assertFalse(valid)
        self.assertIn("gld:sess.sig", reason)

    def test_rejects_empty_session_value(self):
        valid, reason = checkin.validate_cookie("gld:sess=; gld:sess.sig=signature")
        self.assertFalse(valid)
        self.assertIn("gld:sess", reason)

    def test_normalizes_header_prefix_and_quotes(self):
        raw = '"Cookie: gld:sess=current; gld:sess.sig=current-signature"'
        self.assertEqual(
            "gld:sess=current; gld:sess.sig=current-signature",
            checkin.normalize_cookie(raw),
        )
        self.assertTrue(checkin.validate_cookie(raw)[0])

    def test_splits_multiple_accounts(self):
        raw = (
            "gld:sess=one; gld:sess.sig=one-signature ||| "
            "koa:sess=two; koa:sess.sig=two-signature"
        )
        self.assertEqual(2, len(checkin.split_cookie_accounts(raw)))

    def test_accepts_only_supported_https_base_urls(self):
        self.assertEqual(
            "https://glados.space",
            checkin.normalize_base_url("https://glados.space/"),
        )
        with self.assertRaises(ValueError):
            checkin.normalize_base_url("https://example.com")
        with self.assertRaises(ValueError):
            checkin.normalize_base_url("http://glados.cloud")

    def test_unauthorized_status_stops_before_checkin(self):
        session = cast(
            requests.Session,
            types.SimpleNamespace(cookies=types.SimpleNamespace(clear=lambda: None)),
        )
        with mock.patch.object(
            checkin,
            "api_get",
            return_value={"code": -2, "message": "没有权限"},
        ), mock.patch.object(checkin, "checkin_request") as request:
            result = checkin.checkin_account(
                session,
                "gld:sess=current; gld:sess.sig=current-signature",
                1,
            )

        request.assert_not_called()
        self.assertEqual("fail", result["result"])
        self.assertIn("Cookie认证失败", result["status"])

    def test_exchange_uses_official_json_request_format(self):
        response = mock.Mock()
        response.json.return_value = {"code": 0, "message": "ok"}
        session = mock.Mock()
        session.post.return_value = response
        headers = {"cookie": "gld:sess=current"}

        result = checkin.exchange_request(session, headers, "plan100")

        self.assertEqual(0, result["code"])
        session.post.assert_called_once_with(
            checkin.EXCHANGE_URL,
            headers=headers,
            json={"planType": "plan100"},
            timeout=checkin.TIMEOUT,
        )

    def test_request_keeps_complete_transition_cookie(self):
        raw = (
            '"Cookie: __stripe_mid=unrelated; '
            "koa:sess=legacy; koa:sess.sig=legacy-signature; "
            'gld:sess=current; gld:sess.sig=current-signature"'
        )
        captured = {}

        def fake_checkin_request(_, headers):
            captured["cookie"] = headers["cookie"]
            return {"code": 1, "message": "Checkin Repeats!"}

        def fake_api_get(_, url, headers):
            del headers
            if url == checkin.STATUS_URL:
                return {"data": {"email": "user@example.com", "leftDays": 10}}
            return {"points": 20}

        session = cast(
            requests.Session,
            types.SimpleNamespace(cookies=types.SimpleNamespace(clear=lambda: None)),
        )
        with mock.patch.object(checkin, "checkin_request", fake_checkin_request), \
                mock.patch.object(checkin, "api_get", fake_api_get):
            result = checkin.checkin_account(session, raw, 1)

        self.assertEqual("repeat", result["result"])
        self.assertEqual(
            "__stripe_mid=unrelated; "
            "koa:sess=legacy; koa:sess.sig=legacy-signature; "
            "gld:sess=current; gld:sess.sig=current-signature",
            captured["cookie"],
        )


if __name__ == "__main__":
    unittest.main()
