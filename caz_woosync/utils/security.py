import base64
import hashlib
import hmac
import ipaddress

import frappe


def verify_webhook_signature(payload_bytes: bytes, signature_header: str, secret: str) -> bool:
    """
    Verify a WooCommerce webhook HMAC-SHA256 signature.
    WooCommerce signs with HMAC-SHA256 (raw binary) then base64-encodes the result:
      PHP: base64_encode(hash_hmac('sha256', $payload, $secret, true))
    """
    if not secret or not signature_header:
        return False
    expected = base64.b64encode(
        hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8").strip()
    sig = (signature_header or "").strip()
    try:
        return hmac.compare_digest(expected, sig)
    except TypeError:
        return False


def get_client_ip() -> str:
    """Extract real client IP from request, respecting X-Real-IP from trusted proxies."""
    request = frappe.local.request
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def is_ip_allowed(client_ip: str, allowed_ranges: str) -> bool:
    """
    Check if client_ip is within the allowed CIDR ranges.
    Empty allowed_ranges means all IPs are allowed.
    """
    if not allowed_ranges or not allowed_ranges.strip():
        return True
    try:
        client = ipaddress.ip_address(client_ip)
    except ValueError:
        frappe.log_error(
            f"CAZ WooSync: could not parse client IP: {client_ip!r}",
            "Webhook IP Check",
        )
        return False
    for line in allowed_ranges.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            if client in ipaddress.ip_network(line, strict=False):
                return True
        except ValueError:
            continue
    return False
