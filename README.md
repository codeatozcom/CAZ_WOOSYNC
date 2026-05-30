# CAZ WooSync

**WooCommerce ↔ ERPNext two-way sync connector.**

Keeps your WooCommerce store and ERPNext instance in sync automatically — products, orders, customers, inventory, prices, accounting, refunds, coupons, and more. Supports multiple stores from a single ERPNext instance.

---

## Features

| Feature | Direction | Description |
|---------|-----------|-------------|
| Simple Products | Both ways | Creates/updates ERPNext Items from WooCommerce products and vice versa |
| Variable Products | Woo → ERP | Syncs product variants as ERPNext Item Templates + Variants |
| Orders | Woo → ERP | WooCommerce orders become ERPNext Sales Orders |
| Customers | Woo → ERP | WooCommerce customers sync to ERPNext Customers, Contacts, Addresses |
| Inventory | Both ways | Stock levels kept in sync between ERPNext Bin and WooCommerce stock_quantity |
| Prices | Both ways | ERPNext Item Price ↔ WooCommerce regular/sale price |
| Accounting | Woo → ERP | Sales Invoices and Payment Entries created from paid orders |
| Refunds | Woo → ERP | WooCommerce refunds create ERPNext credit notes |
| Coupons | Woo → ERP | WooCommerce coupons sync to ERPNext Pricing Rules |
| Bulk Import | Woo → ERP | One-click import of all existing WooCommerce data into ERPNext |
| Multi-store | — | Manage multiple WooCommerce stores from one ERPNext instance |
| Webhooks | — | Real-time sync via HMAC-SHA256 verified webhooks |
| Polling | — | 15-minute cron fallback for missed webhooks |
| Queue Dashboard | — | Monitor sync status, retry failures, inspect errors |
| Alerts | — | Email alerts for failures, daily digest, connection monitoring |

---

## Requirements

- ERPNext v14, v15, or v16
- WooCommerce 7.0+
- WordPress 6.0+
- Python 3.10+

---

## Installation

```bash
# From your ERPNext bench directory
bench get-app https://github.com/codeatozcom/caz_woosync
bench --site your-site.local install-app caz_woosync
bench --site your-site.local migrate
bench restart
```

---

## Quick Setup (5 minutes)

### Step 1 — WooCommerce REST API Key

1. Go to **WordPress Admin → WooCommerce → Settings → Advanced → REST API**
2. Click **Add Key**
3. Set Description: `CAZ WooSync`, User: `admin`, Permissions: `Read/Write`
4. Click **Generate API Key**
5. Copy the **Consumer Key** and **Consumer Secret**

### Step 2 — ERPNext Store Record

1. Go to **ERPNext → Caz Woosync → Caz Woo Store → New**
2. Fill in:
   - **WooCommerce URL** — your WordPress site URL (e.g. `https://myshop.com`)
   - **Consumer Key** — `ck_...` from Step 1
   - **Consumer Secret** — `cs_...` from Step 1
   - **Company** — your ERPNext company
3. Click **Save** — webhook secret is auto-generated
4. Click **Test Connection** — should show "Connected"
5. Click **Install Webhooks** — registers 6 webhooks in WooCommerce automatically

### Step 3 — WordPress Plugin (optional but recommended)

1. Upload the `woo-plugin/` folder to `wp-content/plugins/caz-woosync/`
2. Activate **CAZ WooSync** in WordPress Admin → Plugins
3. Go to **WooCommerce → Settings → CAZ WooSync**
4. Enter your ERPNext URL, API Key, and API Secret
5. Click **Save Changes** — webhooks are installed automatically
6. Click **Test Connection to ERPNext**

### Step 4 — Bulk Import Existing Data

1. Go to **ERPNext → Caz Woosync → Caz Woo Import**
2. Select your store
3. Check Products, Orders, Customers
4. Click **Start Import**

---

## Configuration

### Global Settings

Go to **ERPNext → Caz Woosync → Caz Woo Settings**:

| Setting | Default | Description |
|---------|---------|-------------|
| Queue Batch Size | 50 | Items processed per queue run |
| Max Retry Attempts | 5 | Retries before marking Failed |
| Rate Limit (req/min) | 40 | WooCommerce API rate limit |
| Verify Webhook Signature | On | Validate HMAC-SHA256 on incoming webhooks |
| Alert Email | — | Email for failure alerts and daily digest |
| Send Daily Digest | Off | Daily summary of sync activity |
| Alert on Connection Failure | On | Email when a store goes offline |

### Store Settings

Each **Caz Woo Store** record has per-store overrides:

| Setting | Description |
|---------|-------------|
| Sync Direction | Both Ways / WooCommerce to ERPNext / ERPNext to WooCommerce |
| Item Group | Default ERPNext item group for imported products |
| Warehouse | Warehouse for stock sync |
| Default UOM | Unit of measure for new items (default: Nos) |
| Income Account | Account for sales revenue |
| Create Items from Woo | Auto-create ERPNext items for new WooCommerce products |
| Push Items Trigger | On Save / Scheduled / Manual |

---

## Sync Queue

Go to **ERPNext → Caz Woosync → Caz Woo Queue** to monitor all sync activity:

- Filter by store, direction, entity type, status
- Retry failed items individually or in bulk
- View full error log for each failure
- Auto-refreshes every 30 seconds

### Queue Statuses

| Status | Meaning |
|--------|---------|
| Queued | Waiting to be processed |
| Processing | Currently running |
| Done | Completed successfully |
| Failed | All retries exhausted |
| Skipped | Manually skipped |

---

## Scheduler Jobs

| Schedule | Job |
|----------|-----|
| Every 5 minutes | Process sync queue |
| Every 15 minutes | Poll WooCommerce for new/updated records |
| Daily | Health check, connection test, daily digest email |

---

## ERPNext API Keys (for WordPress plugin)

1. Go to **ERPNext → Settings → Users → Administrator**
2. Scroll to **API Access**
3. Click **Generate Keys**
4. Copy the **API Key** and **API Secret**

Or via URL: `http://your-erp/api/method/frappe.core.doctype.user.user.generate_keys?user=Administrator`

---

## Troubleshooting

### Connection Test Fails
- Check Consumer Key/Secret are correct
- Ensure WooCommerce REST API is enabled
- For local setups: use your machine's IP instead of `localhost`

### Orders Not Syncing
- Ensure Customers are imported first (Bulk Import → Customers)
- Check **Warehouse** is set on the Caz Woo Store record
- Check **ERPNext → Settings → Error Log** for `CAZ WooSync` entries

### Webhooks Not Arriving
- Click **Install Webhooks** on the store record
- Ensure ERPNext URL is publicly accessible (not behind a firewall)
- For local testing: use ngrok (`ngrok http 8080`) to expose ERPNext

### Queue Stuck on Queued
- Run: `bench --site your-site execute caz_woosync.tasks.process_sync_queue`
- Check background workers are running: `bench doctor`

### Products Showing as Out of Stock after Import
- This is fixed in v1.0.0 — the 60-second guard prevents import loops
- Manually restore stock in WooCommerce if affected by an older version

---

## Testing

```bash
pip install pytest
python -m pytest tests/ -v
# 613 tests, all passing
```

---

## License

MIT License — see [LICENSE](LICENSE) file.

---

## Support

- GitHub Issues: [codeatozcom/caz_woosync/issues](https://github.com/codeatozcom/caz_woosync/issues)
- Email: support@codeatoz.com
