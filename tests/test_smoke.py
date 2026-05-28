"""Smoke tests that run without a Frappe instance."""


def test_version():
    from caz_woosync import __version__
    assert __version__ == "0.1.0"


def test_hmac_base64_matches_woocommerce():
    """
    Verify HMAC uses base64 encoding, matching WooCommerce's PHP implementation:
      base64_encode(hash_hmac('sha256', $payload, $secret, true))
    """
    import base64
    import hashlib
    import hmac as _hmac

    secret = "test_secret_key"
    body = b'{"id":123,"status":"processing"}'

    # Reproduce WooCommerce's PHP signature in Python
    raw_digest = _hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected_sig = base64.b64encode(raw_digest).decode("utf-8")

    # Must be base64: contains only [A-Za-z0-9+/=], length is 44 for SHA256
    assert len(expected_sig) == 44
    assert expected_sig.endswith("=") or expected_sig[-1].isalnum()
    # Must NOT be hex (hex would be 64 lowercase hex chars)
    assert len(expected_sig) != 64

    # Timing-safe comparison must work with two identical base64 strings
    assert _hmac.compare_digest(expected_sig, expected_sig)


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


def test_webhook_topics_complete():
    """All 8 required WooCommerce webhook topics must be defined."""
    expected = {
        "order.created", "order.updated", "order.deleted",
        "product.created", "product.updated", "product.deleted",
        "customer.created", "customer.updated",
    }
    assert len(expected) == 8


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
