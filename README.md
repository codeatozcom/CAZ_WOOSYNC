# CAZ WooSync

Real-time WooCommerce sync for ERPNext v14, v15 and v16.

## Features

- Real-time order sync via webhooks with cron fallback
- Bidirectional product sync with variant support
- Inventory rules: buffer stock, hide vs zero, multi-warehouse
- Full accounting: payment gateway → journal mapping
- Complete refund loop: WooCommerce refund → ERPNext credit note
- Sync queue dashboard with retry and error detail
- 10-minute setup wizard

## Requirements

- ERPNext v14, v15, or v16
- WooCommerce 7.0+
- WordPress 6.0+
- PHP 7.4–8.3

## Installation

### Frappe Cloud
Install directly from the Frappe Marketplace.

### Self-hosted
```bash
bench get-app caz_woosync https://github.com/codeatoz/caz-woosync
bench --site yoursite.local install-app caz_woosync
```

## Support
https://codeatoz.com/support/caz-woosync

## License
MIT
