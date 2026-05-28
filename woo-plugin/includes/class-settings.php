<?php
defined( 'ABSPATH' ) || exit;

class CAZ_WooSync_Settings {

	public function __construct() {
		add_filter( 'woocommerce_settings_tabs_array', [ $this, 'add_settings_tab' ], 50 );
		add_action( 'woocommerce_settings_tabs_caz_woosync', [ $this, 'render_settings' ] );
		add_action( 'woocommerce_update_options_caz_woosync', [ $this, 'save_settings' ] );
		add_action( 'admin_enqueue_scripts', [ $this, 'enqueue_scripts' ] );
		add_action( 'wp_ajax_caz_woosync_test_connection', [ $this, 'ajax_test_connection' ] );
	}

	public function add_settings_tab( $tabs ) {
		$tabs['caz_woosync'] = __( 'CAZ WooSync', 'caz-woosync' );
		return $tabs;
	}

	public function render_settings() {
		woocommerce_admin_fields( $this->get_settings() );
	}

	public function save_settings() {
		woocommerce_update_options( $this->get_settings() );
	}

	public function get_settings() {
		return [
			[
				'title' => __( 'ERPNext Connection', 'caz-woosync' ),
				'type'  => 'title',
				'desc'  => __( 'Configure the connection between WooCommerce and your ERPNext instance.', 'caz-woosync' ),
				'id'    => 'caz_woosync_erp_section',
			],
			[
				'title'    => __( 'ERPNext Instance URL', 'caz-woosync' ),
				'desc'     => __( 'Your ERPNext URL, e.g. https://yoursite.frappe.cloud or https://erp.yourcompany.com. Must be publicly accessible over HTTPS.', 'caz-woosync' ),
				'id'       => 'caz_woosync_erp_url',
				'type'     => 'url',
				'default'  => '',
				'desc_tip' => true,
				'css'      => 'min-width:400px;',
			],
			[
				'title'    => __( 'API Key', 'caz-woosync' ),
				'desc'     => __( 'ERPNext API Key. Generate in ERPNext > Settings > Users > [your user] > API Access > Generate Keys.', 'caz-woosync' ),
				'id'       => 'caz_woosync_api_key',
				'type'     => 'text',
				'default'  => '',
				'desc_tip' => true,
				'css'      => 'min-width:400px;',
			],
			[
				'title'    => __( 'API Secret', 'caz-woosync' ),
				'desc'     => __( 'ERPNext API Secret. Generated alongside the API Key. Keep this private — do not share it.', 'caz-woosync' ),
				'id'       => 'caz_woosync_api_secret',
				'type'     => 'password',
				'default'  => '',
				'desc_tip' => true,
				'css'      => 'min-width:400px;',
			],
			[
				'type' => 'sectionend',
				'id'   => 'caz_woosync_erp_section',
			],
			[
				'title' => __( 'Sync Behaviour', 'caz-woosync' ),
				'type'  => 'title',
				'id'    => 'caz_woosync_sync_section',
			],
			[
				'title'    => __( 'Sync Direction', 'caz-woosync' ),
				'desc'     => __( 'Both Ways: keeps WooCommerce and ERPNext in sync automatically. WooCommerce → ERPNext: WooCommerce is the master, ERPNext follows. ERPNext → WooCommerce: ERPNext is the master, WooCommerce follows.', 'caz-woosync' ),
				'id'       => 'caz_woosync_sync_direction',
				'type'     => 'select',
				'default'  => 'both',
				'options'  => [
					'both'    => __( 'Both Ways', 'caz-woosync' ),
					'woo_erp' => __( 'WooCommerce → ERPNext only', 'caz-woosync' ),
					'erp_woo' => __( 'ERPNext → WooCommerce only', 'caz-woosync' ),
				],
				'desc_tip' => true,
			],
			[
				'title'   => __( 'Enable Sync', 'caz-woosync' ),
				'desc'    => __( 'Enable real-time sync between WooCommerce and ERPNext. Uncheck to pause all sync activity.', 'caz-woosync' ),
				'id'      => 'caz_woosync_enabled',
				'type'    => 'checkbox',
				'default' => 'yes',
			],
			[
				'type' => 'sectionend',
				'id'   => 'caz_woosync_sync_section',
			],
		];
	}

	public function enqueue_scripts( $hook ) {
		if ( 'woocommerce_page_wc-settings' !== $hook ) {
			return;
		}
		if ( ! isset( $_GET['tab'] ) || 'caz_woosync' !== sanitize_key( $_GET['tab'] ) ) {
			return;
		}
		wp_enqueue_script(
			'caz-woosync-settings',
			CAZ_WOOSYNC_PLUGIN_URL . 'admin/js/settings.js',
			[ 'jquery' ],
			CAZ_WOOSYNC_VERSION,
			true
		);
		wp_localize_script(
			'caz-woosync-settings',
			'cazWooSync',
			[
				'ajax_url' => admin_url( 'admin-ajax.php' ),
				'nonce'    => wp_create_nonce( 'caz_woosync_test' ),
				'i18n'     => [
					'testing'   => __( 'Testing...', 'caz-woosync' ),
					'connected' => __( 'Connected successfully.', 'caz-woosync' ),
					'failed'    => __( 'Connection failed: ', 'caz-woosync' ),
				],
			]
		);
	}

	public function ajax_test_connection() {
		check_ajax_referer( 'caz_woosync_test', 'nonce' );
		if ( ! current_user_can( 'manage_woocommerce' ) ) {
			wp_send_json_error( [ 'message' => 'Insufficient permissions.' ] );
		}

		$erp_url    = sanitize_url( get_option( 'caz_woosync_erp_url', '' ) );
		$api_key    = sanitize_text_field( get_option( 'caz_woosync_api_key', '' ) );
		$api_secret = sanitize_text_field( get_option( 'caz_woosync_api_secret', '' ) );

		if ( empty( $erp_url ) || empty( $api_key ) || empty( $api_secret ) ) {
			wp_send_json_error( [ 'message' => 'Please fill in ERPNext URL, API Key, and API Secret before testing.' ] );
		}

		$response = wp_remote_get(
			trailingslashit( $erp_url ) . 'api/method/frappe.auth.get_logged_user',
			[
				'headers'   => [
					'Authorization' => 'token ' . $api_key . ':' . $api_secret,
				],
				'timeout'   => 15,
				'sslverify' => true,
			]
		);

		if ( is_wp_error( $response ) ) {
			wp_send_json_error( [ 'message' => 'Could not reach ERPNext: ' . $response->get_error_message() ] );
		}

		$code = wp_remote_retrieve_response_code( $response );
		if ( 200 === $code ) {
			$body = json_decode( wp_remote_retrieve_body( $response ), true );
			wp_send_json_success( [ 'message' => 'Connected to ERPNext as ' . ( $body['message'] ?? 'unknown user' ) ] );
		} else {
			wp_send_json_error( [ 'message' => 'ERPNext returned HTTP ' . $code . '. Check your API Key and Secret.' ] );
		}
	}
}
