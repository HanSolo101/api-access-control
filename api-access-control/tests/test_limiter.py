import hmac
import hashlib
import secrets
from datetime import datetime, timezone


def generate_key_string() -> str:
    """A10: Format qk_<8-char id>_<43-char secret>."""
    key_id = secrets.token_hex(4)
    key_secret = secrets.token_urlsafe(32)[:43]
    return f"qk_{key_id}_{key_secret}"


def hmac_hash(secret: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()


def constant_time_compare(val1: str, val2: str) -> bool:
    return hmac.compare_digest(val1, val2)


def get_current_utc_month_info():
    now = datetime.now(timezone.utc)
    yyyymm = now.strftime("%Y%m")
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        next_month = datetime(now.year, now.month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    seconds_until_next_month = int((next_month - now).total_seconds())
    reset_at = int(next_month.timestamp())
    return yyyymm, max(1, seconds_until_next_month), reset_at


def test_key_format():
    key = generate_key_string()
    parts = key.split("_")
    assert len(parts) == 3, f"Expected 3 parts, got {len(parts)}"
    assert parts[0] == "qk", f"Expected prefix 'qk', got {parts[0]}"
    assert len(parts[1]) == 8, f"Expected 8 chars for id, got {len(parts[1])}"
    assert len(parts[2]) == 43, f"Expected 43 chars for secret, got {len(parts[2])}"
    print("[PASS] test_key_format")


def test_hmac_hash_and_constant_time_compare():
    secret = "qk_12345678_abcdefghijklmnopqrstuvwxyz0123456789ABCDE"
    pepper = "super-secret-pepper"
    wrong_pepper = "different-pepper"

    h1 = hmac_hash(secret, pepper)
    h2 = hmac_hash(secret, pepper)
    h_wrong = hmac_hash(secret, wrong_pepper)

    assert constant_time_compare(h1, h2) is True
    assert constant_time_compare(h1, h_wrong) is False
    print("[PASS] test_hmac_hash_and_constant_time_compare")


def test_utc_month_info():
    yyyymm, seconds_until_next, reset_at = get_current_utc_month_info()
    assert len(yyyymm) == 6
    assert yyyymm.isdigit()
    assert seconds_until_next > 0
    assert reset_at > 0
    print("[PASS] test_utc_month_info")


if __name__ == "__main__":
    test_key_format()
    test_hmac_hash_and_constant_time_compare()
    test_utc_month_info()
    print("\nAll core security & quota calculation unit tests passed!")
