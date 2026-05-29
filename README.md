# CAZ WooSync

Real-time WooCommerce sync for ERPNext v14, v15, and v16. Keeps your WooCommerce store and ERPNext instance in sync automatically — products, orders, customers, inventory, prices, accounting, refunds, coupons, and more.

## Features

All 13 phases implemented:

| Phase | Feature |
|-------|---------|
| 1 | Core sync engine — bidirectional product sync with variant support |
| 2 | Order sync — WooCommerce orders to ERPNext Sales Orders |
| 3 | Customer sync — WooCommerce customers to ERPNext Customers |
| 4 | Inventory sync — ERPNext stock levels pushed to WooCommerce |
| 5 | Price sync — ERPNext price lists pushed to WooCommerce |
| 6 | Accounting — payment gateway to journal entry mapping |
| 7 | Refunds — WooCommerce refund to ERPNext credit note loop |
| 8 | Coupons — WooCommerce coupon sync to ERPNext Pricing Rules |
| 9 | Bulk import wizard — import all products, orders, customers from WooCommerce |
| 10 | Multi-store dashboard — manage multiple WooCommerce stores from one ERPNext instance |
| 11 | Sync queue page — monitor queue status, retry failures, inspect errors |
| 12 | Webhooks + security — HMAC-SHA256 webhook verification, IP allowlist |
| 13 | Alerts and notifications — email/in-app alerts for failures, daily digest, connection monitoring |

## Requirements

- ERPNext v14, v15, or v16
- WooCommerce 7.0+
- WordPress 6.0+
- Python 3.10+

## Installation

```bash
# From the ERPNext bench directory
bench get-app https://github.com/your-org/caz_woosync
bench --site your-site.local install-app caz_woosync
bench --site your-site.local migrate
bench restart
```

## Configuration

### 1. Add a Store

1. Go to **CAZ Woo Store** and create a new record.
2. Enter your WooCommerce store URL (e.g. `https://myshop.com`).
3. Enter Consumer Key and Consumer Secret from **WooCommerce > Settings > Advanced > REST API**.
4. Save. The Webhook URL field will auto-populate.
5. Click **Test Connection** to verify credentials.

### 2. Install Webhooks

In the store record, click **Install Webhooks**. This creates 8 webhooks in WooCommerce (order created/updated/deleted, product created/updated/deleted, customer created/updated).

Alternatively, add the webhook URL manually in **WooCommerce > Settings > Advanced > Webhooks** for each topic.

### 3. Global Settings

Go to **Caz Woo Settings** to configure:

- **Rate Limiting**: max API requests per minute, queue batch size, retry attempts.
- **Security**: enable webhook signature verification (recommended), restrict to specific IPs.
- **Logging & Alerts**: enable debug logging, set alert email for failure notifications.
- **Alert Settings**: configure daily digest, digest hour, connection failure alerts, and failure threshold.

### 4. Alert Settings

| Setting | Default | Description |
|---------|---------|-------------|
| Alert Email | — | Email to receive alerts and digest. Leave blank to disable. |
| Send Daily Digest | Off | Send a daily summary of sync activity. |
| Digest Hour | 8 | Hour of day (0–23) to send the digest. |
| Alert on Connection Failure | On | Email when a store connection test fails. |
| Alert Threshold (Failures) | 3 | Alert after this many consecutive failures for the same item. |

## Supported Sync Features

| Feature | Direction | Notes |
|---------|-----------|-------|
| Simple products | Both | Creates/updates ERPNext Items |
| Variable products | Woo → ERP | Variants mapped as item variants |
| Orders | Woo → ERP | Maps to Sales Orders and Invoices |
| Customers | Woo → ERP | Maps to ERPNext Customers |
| Inventory | ERP → Woo | Buffer stock, hide vs zero, multi-warehouse |
| Prices | ERP → Woo | Price list mapping per store |
| Refunds | Woo → ERP | Credit notes via WooCommerce refund webhooks |
| Coupons | Woo → ERP | Maps to ERPNext Pricing Rules |
| Accounting | Woo → ERP | Journal entries for payment gateway settlements |

## WooCommerce Plugin Setup

A companion WordPress plugin is included in the `woo-plugin/` directory. It is optional but recommended for:
- Enhanced webhook payloads
- Custom order meta sync
- REST API endpoint extensions

Install it by uploading `woo-plugin/` to your WordPress `wp-content/plugins/` directory and activating it.

## Troubleshooting

### Connection Test Fails

- Verify Store URL does not have a trailing slash issue.
- Check Consumer Key/Secret are for the correct environment.
- Ensure WooCommerce REST API is enabled (**WooCommerce > Settings > Advanced > REST API**).
- Check that the site is publicly accessible (not behind a VPN or local-only).

### Sync Queue Stuck

- Go to the **CAZ WooSync Queue** page to see item statuses.
- Failed items show the full error log.
- Use **Retry** on individual items or clear the queue and re-import.
- Check **Error Log** in ERPNext for `CAZ WooSync` entries.

### Webhooks Not Arriving

- Confirm webhooks are installed (store record > Webhooks tab).
- Check that the Webhook URL is publicly accessible.
- Enable **Verify Webhook Signature** only after confirming the webhook secret matches.
- Use `bench --site your-site.local console` to test manually.

### Daily Digest Not Sending

- Confirm **Alert Email** is set in **Caz Woo Settings**.
- Confirm **Send Daily Digest** is enabled.
- The digest runs via the daily scheduler — ensure `bench schedule` is running.
- Check **Error Log** for `CAZ WooSync digest failed` entries.

## Scheduler Jobs

| Schedule | Job |
|----------|-----|
| Every 5 minutes | Process sync queue |
| Every 15 minutes | Poll WooCommerce for changes (cron fallback) |
| Daily | Health check, connection test, daily digest |

## Testing

```bash
pip install pytest
python -m pytest tests/ -v
```

581+ tests covering all sync phases.
