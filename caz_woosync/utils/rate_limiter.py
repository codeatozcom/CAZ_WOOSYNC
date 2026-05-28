import time

import frappe

BACKOFF_DELAYS = [1, 2, 4, 8]  # seconds; doubles each attempt, 4 max attempts


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
        pipe = cache.pipeline()
        pipe.incr(key)
        pipe.expire(key, 120)
        results = pipe.execute()
        current_count = results[0]
        return current_count <= max_per_minute
    except Exception:
        frappe.log_error(frappe.get_traceback(), "CAZ WooSync: Redis rate limiter error")
        return True  # Fail open if Redis unavailable


def get_woo_client(store_name: str):
    """
    Returns a raw woocommerce.API client (backward compatibility).
    New code should use WooCommerceClient instead.
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


class RateLimitExceeded(Exception):
    pass


class WooCommerceClient:
    """
    Rate-limited WooCommerce API client with exponential backoff on 429/5xx.
    Use this class in all sync modules (Phase 2+).
    """

    def __init__(self, store_name: str):
        self._store_name = store_name
        store = frappe.get_doc("Caz Woo Store", store_name)
        self._rate_limit = store.api_rate_limit or 60
        self._api = get_woo_client(store_name)

    def _call(self, method: str, endpoint: str, **kwargs):
        if not check_rate_limit(self._store_name, self._rate_limit):
            raise RateLimitExceeded(
                f"Rate limit of {self._rate_limit} req/min exceeded for store "
                f"'{self._store_name}'. Sync will resume on next scheduler tick."
            )

        last_exc = None
        for attempt, delay in enumerate(BACKOFF_DELAYS):
            try:
                fn = getattr(self._api, method)
                response = fn(endpoint, **kwargs)
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", delay))
                    frappe.log_error(
                        f"WooCommerce rate limit (429) on {endpoint}. "
                        f"Waiting {retry_after}s (attempt {attempt + 1}/{len(BACKOFF_DELAYS)}).",
                        "CAZ WooSync: Rate Limited",
                    )
                    time.sleep(retry_after)
                    continue
                if response.status_code >= 500:
                    frappe.log_error(
                        f"WooCommerce server error ({response.status_code}) on {endpoint}. "
                        f"Retrying in {delay}s (attempt {attempt + 1}/{len(BACKOFF_DELAYS)}).",
                        "CAZ WooSync: Server Error",
                    )
                    time.sleep(delay)
                    continue
                return response
            except RateLimitExceeded:
                raise
            except Exception as exc:
                last_exc = exc
                frappe.log_error(
                    f"WooCommerce API exception on {endpoint}: {exc}. "
                    f"Retrying in {delay}s.",
                    "CAZ WooSync: API Error",
                )
                time.sleep(delay)

        raise last_exc or RuntimeError(
            f"WooCommerce API call to {endpoint!r} failed after "
            f"{len(BACKOFF_DELAYS)} attempts."
        )

    def get(self, endpoint, **kwargs):
        return self._call("get", endpoint, **kwargs)

    def post(self, endpoint, data=None, **kwargs):
        return self._call("post", endpoint, data=data, **kwargs)

    def put(self, endpoint, data=None, **kwargs):
        return self._call("put", endpoint, data=data, **kwargs)

    def delete(self, endpoint, **kwargs):
        return self._call("delete", endpoint, **kwargs)
