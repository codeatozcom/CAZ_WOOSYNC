# Changelog

## [0.1.0] — 2026-05-28

### Added
- Phase 0 & 1: Project foundation and Connection & Authentication
- Frappe app scaffold (caz_woosync) with hooks, install, tasks
- Caz Woo Store doctype — multi-store connection settings with encrypted credentials
- Caz Woo Settings singleton — global rate limits, security, logging settings
- Caz Woo Sync Queue doctype — event queue foundation for Phase 2 sync engine
- Webhook receiver endpoint with HMAC-SHA256 signature verification
- Auto-install 8 WooCommerce webhooks via REST API
- 5-step setup wizard at /caz-woo-setup
- Dashboard at /caz-woo-dashboard with connection health and queue stats
- WooCommerce plugin (CAZ WooSync for WooCommerce) with settings tab
- GitHub Actions: CI (pytest, PHP syntax) and Linters (flake8, Semgrep, phpcs/WPCS)
