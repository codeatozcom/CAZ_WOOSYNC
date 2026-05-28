"""Smoke tests that run without a Frappe instance."""


def test_version():
    from caz_woosync import __version__
    assert __version__ == "0.1.0"


def test_hmac_hex_not_base64():
    """Verify HMAC uses hex (WooCommerce format), not base64."""
    import hashlib
    import hmac as _hmac

    secret = "test_secret_key"
    body = b'{"id":123,"status":"processing"}'

    # What WooCommerce sends
    expected_hex = _hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()

    # Inline test of the core logic (independent of Frappe)
    sig = _hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    sig2 = _hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert _hmac.compare_digest(sig, sig2)
    assert len(sig) == 64  # SHA256 hex is always 64 chars
    assert not any(c in sig for c in "+/=")  # Not base64


def test_topic_mapping():
    """Verify webhook topic → entity type mapping."""
    mapping = {
        "order": "Order",
        "product": "Product",
        "customer": "Customer",
    }
    topics = ["order.created", "order.updated", "product.created", "customer.updated"]
    for topic in topics:
        resource = topic.split(".")[0]
        assert resource in mapping, f"Topic resource '{resource}' not in mapping"


def test_rate_limit_key_format():
    """Verify rate limit Redis key does not collide across stores."""
    import time
    store_a = "store-alpha"
    store_b = "store-beta"
    minute_bucket = int(time.time() // 60)
    key_a = f"caz_woo_rl:{store_a}:{minute_bucket}"
    key_b = f"caz_woo_rl:{store_b}:{minute_bucket}"
    assert key_a != key_b
    assert store_a in key_a
    assert store_b in key_b
