import time

import frappe


def check_rate_limit(store_name: str, max_per_minute: int) -> bool:
    """
    Atomically increment and check the per-store per-minute request count.
    Returns True if request is within limit, False if rate limit exceeded.
    Uses Redis pipeline for atomic INCR + EXPIRE (no race condition).
    """
    minute_bucket = int(time.time() // 60)
    key = f"caz_woo_rl:{store_name}:{minute_bucket}"

    try:
        cache = frappe.cache()
        # Pipeline makes INCR + EXPIRE atomic
        pipe = cache.pipeline()
        pipe.incr(key)
        pipe.expire(key, 120)  # 2-minute TTL handles clock skew
        results = pipe.execute()
        current_count = results[0]
        return current_count <= max_per_minute
    except Exception:
        # If Redis is unavailable, fail open (allow the request)
        frappe.log_error(frappe.get_traceback(), "CAZ WooSync: Redis rate limiter error")
        return True


def get_woo_client(store_name: str):
    """
    Returns an authenticated WooCommerce API client.
    Credentials are fetched and decrypted from the store record.
    """
    from woocommerce import API as WooAPI

    store = frappe.get_doc("Caz Woo Store", store_name)
    consumer_secret = store.get_password("consumer_secret")
    if not consumer_secret:
        frappe.throw(
            f"Consumer Secret is not set for store '{store_name}'. "
            "Please enter it in CAZ WooSync > Caz Woo Store > API Credentials."
        )
    return WooAPI(
        url=store.woo_url,
        consumer_key=store.consumer_key,
        consumer_secret=consumer_secret,
        version=store.woo_api_version or "wc/v3",
        timeout=30,
    )
