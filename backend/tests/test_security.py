"""Password hashing, JWT and PII redaction."""

import pytest

from app.core.exceptions import TokenExpiredError, UnauthorizedError
from app.core.logging import mask_identifier, scrub_text, scrub_value
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestPasswords:
    def test_roundtrip(self):
        h = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", h)

    def test_wrong_password_rejected(self):
        assert not verify_password("nope", hash_password("secret-value-123"))

    def test_hash_is_salted(self):
        assert hash_password("same") != hash_password("same")

    def test_whitespace_is_significant(self):
        # A password with surrounding spaces is that password; never trimmed.
        h = hash_password(" spaced ")
        assert verify_password(" spaced ", h)
        assert not verify_password("spaced", h)

    def test_malformed_hash_returns_false(self):
        assert not verify_password("x", "not-a-bcrypt-hash")


class TestTokens:
    def test_access_token_roundtrip(self):
        token = create_access_token("11111111-1111-1111-1111-111111111111")
        payload = decode_token(token, "access")
        assert payload["sub"] == "11111111-1111-1111-1111-111111111111"
        assert payload["type"] == "access"

    def test_refresh_token_carries_jti(self):
        token, jti = create_refresh_token("22222222-2222-2222-2222-222222222222")
        assert decode_token(token, "refresh")["jti"] == jti

    def test_access_token_cannot_be_used_as_refresh(self):
        token = create_access_token("33333333-3333-3333-3333-333333333333")
        with pytest.raises(Exception):
            decode_token(token, "refresh")

    def test_garbage_rejected(self):
        with pytest.raises(UnauthorizedError):
            decode_token("not.a.token", "access")


class TestPIIRedaction:
    def test_mask_keeps_last_four(self):
        assert mask_identifier("123456789012") == "********9012"

    def test_aadhaar_masked_in_text(self):
        assert "123456789012" not in scrub_text("Aadhaar is 123456789012 on file")

    def test_spaced_aadhaar_masked(self):
        assert "1234 5678 9012" not in scrub_text("id 1234 5678 9012 here")

    def test_pan_masked_in_text(self):
        assert "ABCDE1234F" not in scrub_text("PAN ABCDE1234F belongs to")

    def test_sensitive_keys_redacted(self):
        assert scrub_value("password", "hunter2") == "<redacted>"
        assert scrub_value("gemini_api_key", "AIzaXYZ") == "<redacted>"

    def test_nested_structures_redacted(self):
        out = scrub_value("fields", {"pan_number": "ABCDE1234F", "name": "Asha"})
        assert out["pan_number"] == "<redacted>"
        assert out["name"] == "Asha"

    def test_ordinary_text_untouched(self):
        assert scrub_text("Employee handbook, page 4") == "Employee handbook, page 4"


class TestAuditIPCoercion:
    """audit_logs.ip_address is INET; a bad value must not 500 the request."""

    def test_valid_ipv4_kept(self):
        from app.modules.auth.repositories.user_repository import coerce_ip

        assert coerce_ip("203.0.113.7") == "203.0.113.7"

    def test_valid_ipv6_kept(self):
        from app.modules.auth.repositories.user_repository import coerce_ip

        assert coerce_ip("2001:db8::1") == "2001:db8::1"

    def test_hostname_dropped(self):
        from app.modules.auth.repositories.user_repository import coerce_ip

        assert coerce_ip("testclient") is None

    def test_attacker_supplied_garbage_dropped(self):
        """X-Forwarded-For is a request header, so it is never trusted."""
        from app.modules.auth.repositories.user_repository import coerce_ip

        for value in ["'; DROP TABLE users;--", "999.999.999.999", "<script>", ""]:
            assert coerce_ip(value) is None

    def test_none_stays_none(self):
        from app.modules.auth.repositories.user_repository import coerce_ip

        assert coerce_ip(None) is None


class TestRateLimitRuleParsing:
    """A mistyped limit in .env must fail at startup, not silently become
    'no limit at all'."""

    def test_parses_a_per_minute_rule(self):
        from app.core.rate_limit import parse_rule

        rule = parse_rule("5/minute", scope="ip")
        assert (rule.limit, rule.window_seconds, rule.scope) == (5, 60, "ip")

    @pytest.mark.parametrize(
        ("value", "seconds"),
        [("1/second", 1), ("10/minute", 60), ("100/hour", 3600), ("1000/day", 86_400)],
    )
    def test_every_supported_window(self, value, seconds):
        from app.core.rate_limit import parse_rule

        assert parse_rule(value, scope="user").window_seconds == seconds

    def test_plural_units_are_accepted(self):
        from app.core.rate_limit import parse_rule

        assert parse_rule("30/minutes", scope="user").window_seconds == 60

    @pytest.mark.parametrize(
        "value", ["", "5", "5/fortnight", "many/minute", "0/minute", "-1/minute", "/minute"]
    )
    def test_nonsense_raises(self, value):
        from app.core.rate_limit import parse_rule

        with pytest.raises(ValueError):
            parse_rule(value, scope="user")

    def test_every_configured_limit_parses(self):
        """Guards the four values that actually ship."""
        from app.core.rate_limit import parse_rule
        from config.settings import settings

        for value in (settings.RATE_LIMIT_LOGIN, settings.RATE_LIMIT_UPLOAD,
                      settings.RATE_LIMIT_CHAT, settings.RATE_LIMIT_DEFAULT):
            assert parse_rule(value, scope="user").limit >= 1


class TestRateLimitFailsOpen:
    """A Redis outage must not take the API down with it."""

    def test_redis_error_allows_the_request(self, monkeypatch):
        import redis

        from app.core import rate_limit

        def boom():
            raise redis.RedisError("connection refused")

        monkeypatch.setattr(rate_limit, "get_redis", boom)

        decision = rate_limit.check("anything", rate_limit.parse_rule("1/minute", scope="ip"))
        assert decision.allowed is True


class TestDisplayMasking:
    """Identity numbers are masked in a chat ANSWER, not just in logs.

    The answer is a display surface: a chat transcript on screen showing a PAN
    number is exactly what docs/security/pii-handling.md § Display asks to
    prevent.
    """

    def test_a_valid_pan_is_masked_keeping_the_tail(self):
        from app.utils.pii import mask_for_display

        result = mask_for_display("The card shows ABCDE1234F as the number.")

        assert "ABCDE1234F" not in result
        assert "234F" in result, "the tail identifies which card without exposing it"

    def test_a_valid_aadhaar_is_masked(self):
        from app.utils.pii import mask_for_display

        result = mask_for_display("Aadhaar 2345 6789 0124 on the card.")

        assert "2345 6789 0124" not in result
        assert "0124" in result

    def test_an_invalid_aadhaar_shaped_number_is_left_alone(self):
        """A 12-digit invoice or order number is not an Aadhaar. Masking it
        would make a correct answer look broken — over-masking has a real cost
        on a surface a person reads."""
        from app.utils.pii import mask_for_display

        text = "Invoice 123456789012 was paid."
        assert mask_for_display(text) == text

    def test_an_invalid_pan_shaped_string_is_left_alone(self):
        from app.utils.pii import mask_for_display

        text = "Reference ABCD1234F is not a PAN."
        assert mask_for_display(text) == text

    def test_ordinary_text_is_untouched(self):
        from app.utils.pii import mask_for_display

        text = "Employees receive twenty-four days of paid annual leave."
        assert mask_for_display(text) == text

    def test_empty_input_is_safe(self):
        from app.utils.pii import mask_for_display

        assert mask_for_display("") == ""

    def test_several_identifiers_in_one_answer_are_all_masked(self):
        from app.utils.pii import mask_for_display

        result = mask_for_display("PAN ABCDE1234F and Aadhaar 2345 6789 0124.")

        assert "ABCDE1234F" not in result
        assert "2345 6789 0124" not in result

    def test_detection_reports_presence_without_the_value(self):
        from app.utils.pii import contains_identifier

        assert contains_identifier("PAN ABCDE1234F") is True
        assert contains_identifier("Invoice 123456789012") is False
        assert contains_identifier("nothing here") is False
