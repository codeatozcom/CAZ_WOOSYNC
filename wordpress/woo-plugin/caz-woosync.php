<?php
/**
 * Plugin Name:          CAZ WooSync for WooCommerce
 * Plugin URI:           https://codeatoz.com/caz-woosync
 * Description:          Real-time WooCommerce sync for ERPNext. Connects your WooCommerce store to ERPNext v14, v15 and v16.
 * Version:              0.1.0
 * Author:               CodeAtoZ
 * Author URI:           https://codeatoz.com
 * License:              MIT
 * Text Domain:          caz-woosync
 * Domain Path:          /languages
 * Requires at least:    6.0
 * Tested up to:         6.5
 * Requires PHP:         7.4
 * WC requires at least: 7.0
 * WC tested up to:      8.9
 */

defined( 'ABSPATH' ) || exit;

define( 'CAZ_WOOSYNC_VERSION', '0.1.0' );
define( 'CAZ_WOOSYNC_PLUGIN_DIR', plugin_dir_path( __FILE__ ) );
define( 'CAZ_WOOSYNC_PLUGIN_URL', plugin_dir_url( __FILE__ ) );
define( 'CAZ_WOOSYNC_PLUGIN_FILE', __FILE__ );

// Declare HPOS compatibility
add_action( 'before_woocommerce_init', function () {
	if ( class_exists( \Automattic\WooCommerce\Utilities\FeaturesUtil::class ) ) {
		\Automattic\WooCommerce\Utilities\FeaturesUtil::declare_compatibility(
			'custom_order_tables',
			__FILE__,
			true
		);
	}
} );

function caz_woosync_check_woocommerce() {
	if ( ! class_exists( 'WooCommerce' ) ) {
		add_action( 'admin_notices', function () {
			echo '<div class="error"><p><strong>CAZ WooSync</strong> requires WooCommerce to be installed and active. '
				. 'Please install WooCommerce first.</p></div>';
		} );
		return false;
	}
	return true;
}

function caz_woosync_init() {
	if ( ! caz_woosync_check_woocommerce() ) {
		return;
	}
	require_once CAZ_WOOSYNC_PLUGIN_DIR . 'includes/class-settings.php';
	new CAZ_WooSync_Settings();
	require_once CAZ_WOOSYNC_PLUGIN_DIR . 'includes/class-sync-status.php';
	new CAZ_WooSync_Sync_Status();
}
add_action( 'plugins_loaded', 'caz_woosync_init' );

register_activation_hook( __FILE__, 'caz_woosync_activate' );
function caz_woosync_activate() {
	if ( ! current_user_can( 'activate_plugins' ) ) {
		return;
	}
	add_option( 'caz_woosync_erp_url', '' );
	add_option( 'caz_woosync_api_key', '' );
	add_option( 'caz_woosync_api_secret', '' );
	add_option( 'caz_woosync_sync_direction', 'both' );
	add_option( 'caz_woosync_enabled', '1' );
}

register_deactivation_hook( __FILE__, 'caz_woosync_deactivate' );
function caz_woosync_deactivate() {
	// Intentionally empty — no destructive cleanup on deactivate
}
