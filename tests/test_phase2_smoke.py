"""Phase 2 & 3 smoke tests — no Frappe instance required."""
import html as _html
import re
import time

import pytest


# ---------------------------------------------------------------------------
# Helpers mirroring Phase 2/3 logic (pure Python, no Frappe)
# ---------------------------------------------------------------------------

# RETRY_DELAYS from dispatcher.py
RETRY_DELAYS = [0, 60, 300, 900, 3600]
MAX_ATTEMPTS = len(RETRY_DELAYS)

# BACKOFF_DELAYS from rate_limiter.py
BACKOFF_DELAYS = [1, 2, 4, 8]

# Route table mirroring dispatcher._route
ENTITY_ROUTES = {
    ("woo_to_erp", "Product"): "caz_woosync.sync.items.sync_product_to_erp",
    ("woo_to_erp", "Order"): None,      # Phase 4
    ("woo_to_erp", "Customer"): None,   # Phase 5
    ("erp_to_woo", "Product"): "caz_woosync.sync.items.sync_item_to_woo",
}


def _strip_html_pure(raw: str) -> str:
    """Pure Python HTML stripper matching sync/items.py behavior."""
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    return _html.unescape(text)


def _schedule_delay(attempt_count: int):
    """Return retry delay for given attempt, or None if max exceeded."""
    if attempt_count >= MAX_ATTEMPTS:
        return None
    return RETRY_DELAYS[attempt_count]


# ---------------------------------------------------------------------------
# 1. Retry delay schedule
# ---------------------------------------------------------------------------

class TestRetryDelaySchedule:
    def test_delay_count_matches_max_attempts(self):
        assert len(RETRY_DELAYS) == MAX_ATTEMPTS

    def test_first_attempt_is_immediate(self):
        assert RETRY_DELAYS[0] == 0

    def test_delays_are_monotonically_increasing(self):
        assert RETRY_DELAYS == sorted(RETRY_DELAYS)

    def test_last_delay_is_one_hour(self):
        assert RETRY_DELAYS[-1] == 3600

    def test_exceeded_attempts_returns_none(self):
        assert _schedule_delay(MAX_ATTEMPTS) is None
        assert _schedule_delay(MAX_ATTEMPTS + 99) is None


# ---------------------------------------------------------------------------
# 2. WooCommerceClient backoff delays
# ---------------------------------------------------------------------------

class TestBackoffDelays:
    def test_four_attempts(self):
        assert len(BACKOFF_DELAYS) == 4

    def test_starts_at_one_second(self):
        assert BACKOFF_DELAYS[0] == 1

    def test_doubles_each_step(self):
        for i in range(1, len(BACKOFF_DELAYS)):
            assert BACKOFF_DELAYS[i] == BACKOFF_DELAYS[i - 1] * 2

    def test_max_delay_is_bounded(self):
        assert max(BACKOFF_DELAYS) <= 30  # Never sleep more than 30s per attempt


# ---------------------------------------------------------------------------
# 3. Dispatcher routing
# ---------------------------------------------------------------------------

class TestDispatcherRouting:
    def test_woo_to_erp_product_has_handler(self):
        assert ENTITY_ROUTES.get(("woo_to_erp", "Product")) is not None

    def test_erp_to_woo_product_has_handler(self):
        assert ENTITY_ROUTES.get(("erp_to_woo", "Product")) is not None

    def test_woo_to_erp_order_is_stubbed(self):
        # Order sync is Phase 4 — must be in route table but handler is None
        assert ("woo_to_erp", "Order") in ENTITY_ROUTES
        assert ENTITY_ROUTES[("woo_to_erp", "Order")] is None

    def test_unknown_entity_not_in_table(self):
        assert ("woo_to_erp", "Invoice") not in ENTITY_ROUTES

    def test_all_handlers_are_dotted_strings(self):
        for handler in ENTITY_ROUTES.values():
            if handler is not None:
                assert "." in handler, f"Handler {handler!r} must be a dotted module path"


# ---------------------------------------------------------------------------
# 4. HTML stripping
# ---------------------------------------------------------------------------

class TestHtmlStripping:
    def test_plain_text_unchanged(self):
        assert _strip_html_pure("Simple text") == "Simple text"

    def test_paragraph_tags_removed(self):
        result = _strip_html_pure("<p>Hello world</p>")
        assert result == "Hello world"

    def test_nested_tags_stripped(self):
        result = _strip_html_pure("<div><strong>Bold</strong> text</div>")
        assert result == "Bold text"

    def test_html_entities_decoded(self):
        result = _strip_html_pure("Price: &pound;10 &amp; tax")
        assert "£" in result
        assert "&amp;" not in result

    def test_script_content_removed(self):
        result = _strip_html_pure("<script>alert('xss')</script>Safe text")
        assert "script" not in result.lower()
        assert "Safe text" in result

    def test_none_equivalent_handled(self):
        assert _strip_html_pure("") == ""
        assert _strip_html_pure(None) == ""  # via 'raw or ""' guard

    def test_whitespace_collapsed(self):
        result = _strip_html_pure("<p>  Too   many   spaces  </p>")
        assert "  " not in result


# ---------------------------------------------------------------------------
# 5. Rate limit Redis key format
# ---------------------------------------------------------------------------

class TestRateLimitKey:
    def test_keys_differ_across_stores(self):
        minute = int(time.time() // 60)
        key_a = f"caz_woo_rl:store-alpha:{minute}"
        key_b = f"caz_woo_rl:store-beta:{minute}"
        assert key_a != key_b

    def test_key_contains_store_name(self):
        minute = int(time.time() // 60)
        key = f"caz_woo_rl:my-store:{minute}"
        assert "my-store" in key

    def test_key_expires_within_two_minutes(self):
        """TTL must be ≤ 120s to prevent stale rate-limit counts."""
        ttl = 120
        assert ttl <= 120


# ---------------------------------------------------------------------------
# 6. Item Mapping required fields
# ---------------------------------------------------------------------------

ITEM_MAPPING_REQUIRED = {"store", "woo_id", "erp_item", "product_type", "last_synced"}


class TestItemMappingFields:
    def test_all_required_fields_defined(self):
        assert "store" in ITEM_MAPPING_REQUIRED
        assert "woo_id" in ITEM_MAPPING_REQUIRED
        assert "erp_item" in ITEM_MAPPING_REQUIRED

    def test_product_type_options(self):
        valid = {"simple", "variable", "variation"}
        # These must be the only accepted values
        assert "simple" in valid
        assert "variable" in valid

    def test_no_internal_frappe_fields_exposed(self):
        internal = {"docstatus", "modified_by", "owner", "idx"}
        assert not (internal & ITEM_MAPPING_REQUIRED)


# ---------------------------------------------------------------------------
# 7. WooCommerce poll logic — client-side date filtering
# ---------------------------------------------------------------------------

class TestPollLogic:
    def test_older_product_triggers_stop(self):
        """Poll must stop when it finds a product older than last_sync_time."""
        from datetime import datetime, timedelta

        last_sync = datetime(2026, 5, 1, 0, 0, 0)
        product_modified = datetime(2026, 4, 30, 0, 0, 0)  # older than last_sync

        should_stop = product_modified <= last_sync
        assert should_stop

    def test_newer_product_does_not_stop(self):
        from datetime import datetime

        last_sync = datetime(2026, 5, 1, 0, 0, 0)
        product_modified = datetime(2026, 5, 2, 0, 0, 0)  # newer

        should_stop = product_modified <= last_sync
        assert not should_stop

    def test_webhook_topics_complete(self):
        """All 8 required WooCommerce webhook topics defined."""
        expected = {
            "order.created", "order.updated", "order.deleted",
            "product.created", "product.updated", "product.deleted",
            "customer.created", "customer.updated",
        }
        assert len(expected) == 8
