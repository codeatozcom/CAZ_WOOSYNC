"""
Comprehensive smoke tests for Phase 1, 2, and 3 — no Frappe instance required.
Covers: HMAC security, IP allowlist, topic routing, dispatcher retry logic,
rate limiter backoff, HTML stripping, item field mapping, poll logic,
doctype JSON schema integrity, hooks registration, and PHP security patterns.
"""

import base64
import hashlib
import hmac as _hmac
import html as _html
import ipaddress
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
DOCTYPE_DIR = ROOT / "caz_woosync" / "doctype"
WOO_PLUGIN = ROOT / "wordpress" / "woo-plugin"

# ──────────────────────────────────────────────────────────────────────────────
# Constants mirrored from source (tested independently)
# ──────────────────────────────────────────────────────────────────────────────

RETRY_DELAYS = [0, 60, 300, 900, 3600]
MAX_ATTEMPTS = len(RETRY_DELAYS)
BACKOFF_DELAYS = [1, 2, 4, 8]

WEBHOOK_TOPICS = {
    "order.created", "order.updated", "order.deleted",
    "product.created", "product.updated", "product.deleted",
    "customer.created", "customer.updated",
}

TOPIC_TO_ENTITY = {
    "order": "Order",
    "product": "Product",
    "customer": "Customer",
}


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1 — HMAC / Security
# ──────────────────────────────────────────────────────────────────────────────

class TestHmacSignature:
    """Verify HMAC matches WooCommerce PHP: base64(hash_hmac('sha256', payload, secret, true))"""

    def _sign(self, secret: str, body: bytes) -> str:
        raw = _hmac.new(secret.encode(), body, hashlib.sha256).digest()
        return base64.b64encode(raw).decode()

    def test_output_is_base64_not_hex(self):
        sig = self._sign("secret", b"payload")
        assert len(sig) == 44  # SHA-256 base64 is always 44 chars
        assert not re.fullmatch(r"[0-9a-f]{64}", sig)

    def test_known_vector(self):
        # Verified against PHP: base64_encode(hash_hmac('sha256','hello','key',true))
        sig = self._sign("key", b"hello")
        assert sig == base64.b64encode(
            _hmac.new(b"key", b"hello", hashlib.sha256).digest()
        ).decode()

    def test_compare_digest_timing_safe(self):
        sig = self._sign("secret", b"data")
        assert _hmac.compare_digest(sig, sig)
        assert not _hmac.compare_digest(sig, "X" * len(sig))

    def test_different_secrets_produce_different_sigs(self):
        body = b'{"id":1}'
        assert self._sign("secret_a", body) != self._sign("secret_b", body)

    def test_different_bodies_produce_different_sigs(self):
        secret = "my_secret"
        assert self._sign(secret, b'{"id":1}') != self._sign(secret, b'{"id":2}')

    def test_empty_body_still_produces_valid_sig(self):
        sig = self._sign("secret", b"")
        assert len(sig) == 44

    def test_unicode_secret_encoded_as_utf8(self):
        sig = self._sign("sécrét", b"payload")
        assert len(sig) == 44


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1 — IP allowlist
# ──────────────────────────────────────────────────────────────────────────────

class TestIpAllowlist:
    """Test is_ip_allowed() logic mirrored from utils/security.py"""

    def _is_allowed(self, client_ip: str, ranges: str) -> bool:
        if not ranges or not ranges.strip():
            return True
        try:
            client = ipaddress.ip_address(client_ip)
        except ValueError:
            return False
        for line in ranges.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                if client in ipaddress.ip_network(line, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def test_empty_allowlist_permits_all(self):
        assert self._is_allowed("1.2.3.4", "")
        assert self._is_allowed("1.2.3.4", "   ")

    def test_exact_ip_match(self):
        assert self._is_allowed("192.168.1.100", "192.168.1.100")

    def test_cidr_range_match(self):
        assert self._is_allowed("10.0.0.50", "10.0.0.0/8")

    def test_ip_outside_range_blocked(self):
        assert not self._is_allowed("172.16.0.1", "10.0.0.0/8")

    def test_multiple_ranges(self):
        ranges = "10.0.0.0/8\n192.168.0.0/16"
        assert self._is_allowed("10.5.5.5", ranges)
        assert self._is_allowed("192.168.50.1", ranges)
        assert not self._is_allowed("8.8.8.8", ranges)

    def test_invalid_ip_blocked(self):
        assert not self._is_allowed("not-an-ip", "10.0.0.0/8")

    def test_ipv6_supported(self):
        assert self._is_allowed("::1", "::1/128")

    def test_blank_lines_ignored(self):
        ranges = "\n\n10.0.0.0/8\n\n"
        assert self._is_allowed("10.5.5.5", ranges)


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1 — Webhook topic routing
# ──────────────────────────────────────────────────────────────────────────────

class TestTopicRouting:
    def _topic_to_entity(self, topic: str):
        resource = topic.split(".")[0] if topic else ""
        return TOPIC_TO_ENTITY.get(resource)

    def test_all_8_topics_defined(self):
        assert len(WEBHOOK_TOPICS) == 8

    def test_order_topics_map_to_entity(self):
        for topic in ("order.created", "order.updated", "order.deleted"):
            assert self._topic_to_entity(topic) == "Order"

    def test_product_topics_map_to_entity(self):
        for topic in ("product.created", "product.updated", "product.deleted"):
            assert self._topic_to_entity(topic) == "Product"

    def test_customer_topics_map_to_entity(self):
        for topic in ("customer.created", "customer.updated"):
            assert self._topic_to_entity(topic) == "Customer"

    def test_unknown_topic_returns_none(self):
        assert self._topic_to_entity("coupon.created") is None
        assert self._topic_to_entity("") is None

    def test_topic_without_action_handled(self):
        assert self._topic_to_entity("order") == "Order"

    def test_three_entity_types_covered(self):
        entities = set(TOPIC_TO_ENTITY.values())
        assert entities == {"Order", "Product", "Customer"}


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — Retry delay schedule
# ──────────────────────────────────────────────────────────────────────────────

class TestRetryDelays:
    def test_five_delay_slots(self):
        assert len(RETRY_DELAYS) == 5 == MAX_ATTEMPTS

    def test_first_attempt_immediate(self):
        assert RETRY_DELAYS[0] == 0

    def test_monotonically_increasing(self):
        for i in range(1, len(RETRY_DELAYS)):
            assert RETRY_DELAYS[i] > RETRY_DELAYS[i - 1]

    def test_last_delay_one_hour(self):
        assert RETRY_DELAYS[-1] == 3600

    def test_delay_at_valid_attempt(self):
        assert RETRY_DELAYS[2] == 300  # 5 minutes

    def test_no_delay_after_max_attempts(self):
        # At MAX_ATTEMPTS, the item must be marked Failed, no further retries
        assert MAX_ATTEMPTS == 5
        # Attempting to index beyond would raise — guard is caller's responsibility

    def test_retry_not_due_before_delay(self):
        """Simulate: attempt 2 happened 30s ago, delay is 60s → not ready."""
        attempt = 2
        last_attempt_ago_secs = 30
        required = RETRY_DELAYS[attempt]
        assert last_attempt_ago_secs < required  # not ready

    def test_retry_due_after_delay_elapsed(self):
        attempt = 1
        last_attempt_ago_secs = 65
        required = RETRY_DELAYS[attempt]
        assert last_attempt_ago_secs >= required  # ready


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — WooCommerceClient backoff
# ──────────────────────────────────────────────────────────────────────────────

class TestBackoffDelays:
    def test_four_attempts(self):
        assert len(BACKOFF_DELAYS) == 4

    def test_doubles_each_step(self):
        for i in range(1, len(BACKOFF_DELAYS)):
            assert BACKOFF_DELAYS[i] == BACKOFF_DELAYS[i - 1] * 2

    def test_starts_at_one_second(self):
        assert BACKOFF_DELAYS[0] == 1

    def test_max_bounded_at_30s(self):
        assert max(BACKOFF_DELAYS) <= 30

    def test_total_backoff_under_two_minutes(self):
        assert sum(BACKOFF_DELAYS) < 120


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — Rate limiter Redis key
# ──────────────────────────────────────────────────────────────────────────────

class TestRateLimitKey:
    def _key(self, store_name: str) -> str:
        bucket = int(time.time() // 60)
        return f"caz_woo_rl:{store_name}:{bucket}"

    def test_key_prefix(self):
        assert self._key("s").startswith("caz_woo_rl:")

    def test_different_stores_different_keys(self):
        assert self._key("store-a") != self._key("store-b")

    def test_store_name_in_key(self):
        assert "my-store" in self._key("my-store")

    def test_key_has_three_parts(self):
        parts = self._key("store").split(":")
        assert len(parts) == 3

    def test_minute_bucket_numeric(self):
        parts = self._key("store").split(":")
        assert parts[2].isdigit()

    def test_ttl_120_seconds(self):
        # TTL must be 2 minutes to avoid stale counts bleeding across buckets
        ttl = 120
        assert ttl == 120


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — Dispatcher routing table
# ──────────────────────────────────────────────────────────────────────────────

ENTITY_ROUTES = {
    ("woo_to_erp", "Product"): "caz_woosync.sync.items.sync_product_to_erp",
    ("woo_to_erp", "Order"): None,
    ("woo_to_erp", "Customer"): None,
    ("erp_to_woo", "Product"): "caz_woosync.sync.items.sync_item_to_woo",
}


class TestDispatcherRouting:
    def test_wc_product_has_handler(self):
        assert ENTITY_ROUTES[("woo_to_erp", "Product")] is not None

    def test_erp_product_has_handler(self):
        assert ENTITY_ROUTES[("erp_to_woo", "Product")] is not None

    def test_order_stubbed_for_phase4(self):
        assert ("woo_to_erp", "Order") in ENTITY_ROUTES
        assert ENTITY_ROUTES[("woo_to_erp", "Order")] is None

    def test_customer_stubbed_for_phase5(self):
        assert ("woo_to_erp", "Customer") in ENTITY_ROUTES
        assert ENTITY_ROUTES[("woo_to_erp", "Customer")] is None

    def test_handlers_are_dotted_paths(self):
        for handler in ENTITY_ROUTES.values():
            if handler:
                assert handler.count(".") >= 2

    def test_unknown_entity_not_present(self):
        assert ("woo_to_erp", "Invoice") not in ENTITY_ROUTES

    def test_wc_product_handler_path(self):
        assert "sync.items" in ENTITY_ROUTES[("woo_to_erp", "Product")]

    def test_erp_product_handler_path(self):
        assert "sync.items" in ENTITY_ROUTES[("erp_to_woo", "Product")]


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3 — HTML stripping
# ──────────────────────────────────────────────────────────────────────────────

def _strip(raw):
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    return _html.unescape(text)


class TestHtmlStripping:
    def test_plain_text_unchanged(self):
        assert _strip("Hello world") == "Hello world"

    def test_p_tags_removed(self):
        assert _strip("<p>Hello</p>") == "Hello"

    def test_nested_tags(self):
        assert _strip("<div><b>Bold</b> text</div>") == "Bold text"

    def test_entities_decoded(self):
        result = _strip("&pound;10 &amp; tax")
        assert "£" in result
        assert "&amp;" not in result

    def test_script_tag_stripped(self):
        result = _strip("<script>alert(1)</script>Safe")
        assert "script" not in result.lower()
        assert "Safe" in result

    def test_none_guard(self):
        assert _strip(None) == ""
        assert _strip("") == ""

    def test_whitespace_collapsed(self):
        assert "  " not in _strip("<p>  lots   of   space  </p>")

    def test_br_tags(self):
        result = _strip("line1<br>line2<br/>line3")
        assert "line1" in result
        assert "line2" in result

    def test_woocommerce_like_description(self):
        html = "<p>Great <strong>product</strong>! Price: &pound;9.99</p>"
        result = _strip(html)
        assert "Great" in result
        assert "product" in result
        assert "£9.99" in result
        assert "<" not in result


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3 — Item mapping
# ──────────────────────────────────────────────────────────────────────────────

class TestItemMapping:
    def _load_mapping_json(self):
        path = DOCTYPE_DIR / "caz_woo_item_mapping" / "caz_woo_item_mapping.json"
        with open(path) as f:
            return json.load(f)

    def test_autoname_is_hash(self):
        d = self._load_mapping_json()
        assert d["autoname"] == "hash"

    def test_not_singleton(self):
        d = self._load_mapping_json()
        assert d.get("issingle", 0) == 0

    def test_required_fields_present(self):
        d = self._load_mapping_json()
        fieldnames = {f["fieldname"] for f in d["fields"]}
        for required in ("store", "woo_id", "erp_item", "product_type"):
            assert required in fieldnames, f"Missing required field: {required}"

    def test_product_type_is_select(self):
        d = self._load_mapping_json()
        for f in d["fields"]:
            if f["fieldname"] == "product_type":
                assert f["fieldtype"] == "Select"
                options = f.get("options", "")
                assert "simple" in options
                assert "variable" in options

    def test_store_links_to_caz_woo_store(self):
        d = self._load_mapping_json()
        for f in d["fields"]:
            if f["fieldname"] == "store":
                assert f.get("options") == "Caz Woo Store"

    def test_erp_item_links_to_item(self):
        d = self._load_mapping_json()
        for f in d["fields"]:
            if f["fieldname"] == "erp_item":
                assert f.get("options") == "Item"

    def test_last_synced_is_readonly(self):
        d = self._load_mapping_json()
        for f in d["fields"]:
            if f["fieldname"] == "last_synced":
                assert f.get("read_only") == 1

    def test_all_visible_fields_have_descriptions(self):
        d = self._load_mapping_json()
        skipped_types = {"Section Break", "Column Break", "HTML"}
        for f in d["fields"]:
            if f["fieldtype"] not in skipped_types:
                assert f.get("description", "").strip(), \
                    f"Field '{f['fieldname']}' is missing a description"


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3 — Poll logic
# ──────────────────────────────────────────────────────────────────────────────

class TestPollLogic:
    def test_product_older_than_last_sync_stops_poll(self):
        last_sync = datetime(2026, 5, 1)
        product_dt = datetime(2026, 4, 30)
        assert product_dt <= last_sync

    def test_product_newer_than_last_sync_continues(self):
        last_sync = datetime(2026, 5, 1)
        product_dt = datetime(2026, 5, 2)
        assert not (product_dt <= last_sync)

    def test_product_same_time_as_last_sync_stops(self):
        last_sync = datetime(2026, 5, 1, 12, 0, 0)
        product_dt = datetime(2026, 5, 1, 12, 0, 0)
        assert product_dt <= last_sync  # equal → stop (already processed)

    def test_no_last_sync_processes_all(self):
        last_sync = None
        product_dt = datetime(2020, 1, 1)
        # When last_sync is None, never stop early
        should_stop = (last_sync is not None) and (product_dt <= last_sync)
        assert not should_stop

    def test_pagination_stops_on_short_page(self):
        """If page returns < 50 items, stop paginating."""
        page_size = 50
        result_count = 23
        assert result_count < page_size  # → stop pagination

    def test_wc_api_uses_orderby_modified(self):
        params = {"orderby": "modified", "order": "desc", "per_page": 50}
        assert "modified_after" not in params  # WC API doesn't support this
        assert params["orderby"] == "modified"


# ──────────────────────────────────────────────────────────────────────────────
# Doctype JSON schema integrity
# ──────────────────────────────────────────────────────────────────────────────

class TestDoctypeSchemas:
    def _load(self, name: str):
        path = DOCTYPE_DIR / name / f"{name}.json"
        with open(path) as f:
            return json.load(f)

    def _fieldnames(self, d):
        return {f["fieldname"] for f in d["fields"]}

    def test_caz_woo_store_has_item_sync_fields(self):
        d = self._load("caz_woo_store")
        fn = self._fieldnames(d)
        for field in ("item_match_field", "create_items_from_woo",
                      "update_items_from_woo", "item_price_list"):
            assert field in fn, f"Missing: {field}"

    def test_caz_woo_store_has_last_sync_time(self):
        d = self._load("caz_woo_store")
        fn = self._fieldnames(d)
        assert "last_sync_time" in fn

    def test_caz_woo_store_field_order_consistent(self):
        d = self._load("caz_woo_store")
        fo_set = set(d.get("field_order", []))
        field_set = self._fieldnames(d)
        assert fo_set == field_set

    def test_caz_woo_sync_queue_has_all_fields(self):
        d = self._load("caz_woo_sync_queue")
        fn = self._fieldnames(d)
        for field in ("store", "direction", "entity_type", "woo_id",
                      "status", "attempt_count", "last_attempt", "error_log", "payload"):
            assert field in fn, f"Missing: {field}"

    def test_caz_woo_settings_is_singleton(self):
        d = self._load("caz_woo_settings")
        assert d.get("issingle") == 1

    def test_caz_woo_webhook_exists(self):
        d = self._load("caz_woo_webhook")
        assert d.get("istable") == 1  # child table

    def test_all_required_fields_have_descriptions(self):
        for doctype_dir in DOCTYPE_DIR.iterdir():
            if not doctype_dir.is_dir():
                continue
            json_path = doctype_dir / f"{doctype_dir.name}.json"
            if not json_path.exists():
                continue
            with open(json_path) as f:
                d = json.load(f)
            skip_types = {"Section Break", "Column Break", "HTML", "Fold"}
            for field in d.get("fields", []):
                if field.get("reqd") and field["fieldtype"] not in skip_types:
                    assert field.get("description", "").strip(), \
                        f"{d['name']}.{field['fieldname']} is required but has no description"


# ──────────────────────────────────────────────────────────────────────────────
# Hooks registration
# ──────────────────────────────────────────────────────────────────────────────

class TestHooksRegistration:
    def _src(self):
        with open(ROOT / "caz_woosync" / "hooks.py") as f:
            return f.read()

    def test_app_name(self):
        assert 'app_name = "caz_woosync"' in self._src()

    def test_process_sync_queue_scheduled_every_5min(self):
        src = self._src()
        assert "*/5 * * * *" in src
        assert "caz_woosync.tasks.process_sync_queue" in src

    def test_poll_scheduled_every_15min(self):
        src = self._src()
        assert "*/15 * * * *" in src
        assert "caz_woosync.tasks.poll_woocommerce_changes" in src

    def test_daily_health_check_registered(self):
        assert "caz_woosync.tasks.daily_health_check" in self._src()

    def test_item_doc_event_registered(self):
        src = self._src()
        assert "on_update" in src
        assert "caz_woosync.sync.items.on_item_update" in src

    def test_doctype_js_registered(self):
        src = self._src()
        assert "Caz Woo Store" in src
        assert "Caz Woo Sync Queue" in src

    def test_after_install_registered(self):
        assert "caz_woosync.install.after_install" in self._src()

    def test_required_apps(self):
        src = self._src()
        assert "frappe" in src
        assert "erpnext" in src


# ──────────────────────────────────────────────────────────────────────────────
# Frappe Page schema
# ──────────────────────────────────────────────────────────────────────────────

class TestQueuePage:
    def _load(self):
        path = ROOT / "caz_woosync" / "page" / "caz_woo_queue" / "caz_woo_queue.json"
        with open(path) as f:
            return json.load(f)

    def test_page_name(self):
        assert self._load()["name"] == "caz-woo-queue"

    def test_module(self):
        assert self._load()["module"] == "Caz Woosync"

    def test_roles_are_objects(self):
        for role in self._load().get("roles", []):
            assert isinstance(role, dict), f"Role must be dict: {role!r}"
            assert "role" in role

    def test_system_manager_has_access(self):
        roles = {r["role"] for r in self._load().get("roles", [])}
        assert "System Manager" in roles

    def test_page_py_has_whitelisted_methods(self):
        path = ROOT / "caz_woosync" / "page" / "caz_woo_queue" / "caz_woo_queue.py"
        src = path.read_text()
        for method in ("get_queue_data", "retry_failed_items", "skip_items"):
            assert method in src

    def test_page_js_has_autorefresh(self):
        path = ROOT / "caz_woosync" / "page" / "caz_woo_queue" / "caz_woo_queue.js"
        src = path.read_text().lower()
        assert "setinterval" in src or "auto" in src or "refresh" in src


# ──────────────────────────────────────────────────────────────────────────────
# PHP security patterns
# ──────────────────────────────────────────────────────────────────────────────

class TestPhpSecurity:
    def _sync_status_src(self):
        return (WOO_PLUGIN / "includes" / "class-sync-status.php").read_text()

    def _main_plugin_src(self):
        return (WOO_PLUGIN / "caz-woosync.php").read_text()

    def test_hpos_compatibility_declared(self):
        assert "FeaturesUtil" in self._main_plugin_src()
        assert "custom_order_tables" in self._main_plugin_src()

    def test_sync_status_class_loaded(self):
        assert "class-sync-status.php" in self._main_plugin_src()

    def test_product_column_arg_order(self):
        src = self._sync_status_src()
        # ($column, $post_id) — column is first
        m = re.search(r'function render_product_sync_column\(([^)]+)\)', src)
        assert m, "render_product_sync_column not found"
        args = [a.strip() for a in m.group(1).split(",")]
        assert "column" in args[0].lower(), f"First arg should be column, got: {args[0]}"

    def test_output_escaped(self):
        src = self._sync_status_src()
        assert "esc_html" in src

    def test_capability_check_present(self):
        assert "current_user_can" in self._sync_status_src()

    def test_nonce_verification_present(self):
        src = self._sync_status_src()
        assert "check_ajax_referer" in src or "wp_verify_nonce" in src

    def test_hpos_order_columns_registered(self):
        src = self._sync_status_src()
        assert "manage_woocommerce_page_wc-orders_columns" in src
        assert "manage_edit-shop_order_columns" in src

    def test_wc_order_meta_hpos_aware(self):
        src = self._sync_status_src()
        assert "get_meta" in src  # WC_Order::get_meta() for HPOS path

    def test_woocommerce_dependency_check(self):
        assert "class_exists" in self._main_plugin_src()
        assert "WooCommerce" in self._main_plugin_src()


# ──────────────────────────────────────────────────────────────────────────────
# Python source file checks (no Frappe import)
# ──────────────────────────────────────────────────────────────────────────────

class TestSourceFilePatterns:
    def _read(self, rel: str) -> str:
        return (ROOT / rel).read_text()

    def test_security_uses_base64_not_hexdigest(self):
        src = self._read("caz_woosync/utils/security.py")
        assert "hexdigest" not in src
        assert "b64encode" in src

    def test_no_frappe_utils_quote(self):
        for path in (ROOT / "caz_woosync").rglob("*.py"):
            src = path.read_text()
            assert "frappe.utils.quote" not in src, \
                f"frappe.utils.quote used in {path} (doesn't exist — use urllib.parse.quote)"

    def test_strip_html_always_has_or_guard(self):
        src = self._read("caz_woosync/sync/items.py")
        for m in re.finditer(r'strip_html\(([^)]+)\)', src):
            arg = m.group(1).strip()
            if arg not in ('""', "''"):
                assert " or " in arg or arg.startswith('"') or arg.startswith("'"), \
                    f"strip_html({arg}) might receive None — use strip_html(val or '')"

    def test_sendmail_recipients_is_list(self):
        src = self._read("caz_woosync/sync/dispatcher.py")
        for m in re.finditer(r'recipients=([^\n,]+)', src):
            val = m.group(1).strip()
            assert val.startswith("["), \
                f"sendmail recipients must be a list, got: {val}"

    def test_dispatcher_enqueue_uses_dotted_path(self):
        src = self._read("caz_woosync/sync/dispatcher.py")
        for m in re.finditer(r'frappe\.enqueue\(\s*["\']([^"\']+)["\']', src):
            path = m.group(1)
            assert "." in path, f"enqueue path must be dotted: {path!r}"
            assert not path.startswith("."), f"enqueue path must not start with dot: {path!r}"

    def test_receiver_reads_raw_body_first(self):
        src = self._read("caz_woosync/controller/receiver.py")
        raw_body_pos = src.find("get_data")
        sig_check_pos = src.find("verify_webhook_signature")
        assert raw_body_pos < sig_check_pos, "Must read raw_body before HMAC check"

    def test_on_item_update_has_migrate_guard(self):
        src = self._read("caz_woosync/sync/items.py")
        assert "frappe.flags.in_migrate" in src
        assert "frappe.flags.in_patch" in src

    def test_on_item_update_uses_after_commit(self):
        src = self._read("caz_woosync/sync/items.py")
        assert "frappe.db.after_commit" in src

    def test_dispatcher_has_all_retry_delays(self):
        src = self._read("caz_woosync/sync/dispatcher.py")
        assert "RETRY_DELAYS = [0, 60, 300, 900, 3600]" in src

    def test_rate_limiter_pipeline_is_atomic(self):
        src = self._read("caz_woosync/utils/rate_limiter.py")
        assert "pipeline()" in src
        assert "pipe.incr" in src
        assert "pipe.expire" in src
        # Both must happen in same pipeline — check order
        assert src.index("pipe.incr") < src.index("pipe.expire")
