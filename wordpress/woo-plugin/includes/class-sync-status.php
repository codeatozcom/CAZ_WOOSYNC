<?php
/**
 * Sync status column and meta box for WooCommerce product and order admin screens.
 *
 * @package CAZ_WooSync
 */

defined( 'ABSPATH' ) || exit;

class CAZ_WooSync_Sync_Status {

	public function __construct() {
		// Product list column
		add_filter( 'manage_product_posts_columns', array( $this, 'add_product_sync_column' ) );
		add_action( 'manage_product_posts_custom_column', array( $this, 'render_product_sync_column' ), 10, 2 );

		// Product edit meta box
		add_action( 'add_meta_boxes_product', array( $this, 'register_product_meta_box' ) );

		// Order list column — HPOS-aware
		$this->register_order_column_hooks();

		// AJAX
		add_action( 'wp_ajax_caz_woosync_get_product_status', array( $this, 'ajax_get_product_status' ) );
		add_action( 'wp_ajax_caz_woosync_trigger_sync', array( $this, 'ajax_trigger_sync' ) );

		// Enqueue admin scripts
		add_action( 'admin_enqueue_scripts', array( $this, 'enqueue_admin_scripts' ) );
	}

	/**
	 * Register order column hooks with HPOS compatibility.
	 */
	private function register_order_column_hooks() {
		if (
			class_exists( '\Automattic\WooCommerce\Utilities\OrderUtil' ) &&
			\Automattic\WooCommerce\Utilities\OrderUtil::custom_orders_table_usage_is_enabled()
		) {
			// HPOS order list
			add_filter( 'manage_woocommerce_page_wc-orders_columns', array( $this, 'add_order_erp_column' ) );
			add_action( 'manage_woocommerce_page_wc-orders_custom_column', array( $this, 'render_order_erp_column_hpos' ), 10, 2 );
		} else {
			// Legacy post-based orders
			add_filter( 'manage_edit-shop_order_columns', array( $this, 'add_order_erp_column' ) );
			add_action( 'manage_shop_order_posts_custom_column', array( $this, 'render_order_erp_column_legacy' ), 10, 2 );
		}
	}

	// -------------------------------------------------------------------------
	// Product list column
	// -------------------------------------------------------------------------

	public function add_product_sync_column( $columns ) {
		// Insert after 'name' column
		$new = array();
		foreach ( $columns as $key => $label ) {
			$new[ $key ] = $label;
			if ( 'name' === $key ) {
				$new['caz_erp_sync'] = __( 'ERPNext', 'caz-woosync' );
			}
		}
		return $new;
	}

	/**
	 * @param string $column  Column name (first arg).
	 * @param int    $post_id Product post ID (second arg).
	 */
	public function render_product_sync_column( $column, $post_id ) {
		if ( 'caz_erp_sync' !== $column ) {
			return;
		}

		$status    = get_post_meta( $post_id, '_caz_woosync_status', true ) ?: 'never';
		$erp_item  = get_post_meta( $post_id, '_caz_woosync_erp_item', true );
		$last_sync = get_post_meta( $post_id, '_caz_woosync_last_sync', true );

		$icons = array(
			'synced'  => '✅',
			'pending' => '⏳',
			'failed'  => '❌',
			'never'   => '⬜',
		);
		$icon = isset( $icons[ $status ] ) ? $icons[ $status ] : '⬜';

		$title = '';
		if ( $last_sync ) {
			/* translators: %s: human-readable time since last sync */
			$title = sprintf( __( 'Last synced %s ago', 'caz-woosync' ), human_time_diff( (int) $last_sync ) );
		}

		echo '<span title="' . esc_attr( $title ) . '">' . esc_html( $icon ) . '</span>';

		if ( $erp_item ) {
			echo '<br><code style="font-size:10px">' . esc_html( $erp_item ) . '</code>';
		}
	}

	// -------------------------------------------------------------------------
	// Product meta box
	// -------------------------------------------------------------------------

	public function register_product_meta_box() {
		add_meta_box(
			'caz_woosync_product_status',
			__( 'CAZ WooSync', 'caz-woosync' ),
			array( $this, 'render_product_meta_box' ),
			'product',
			'side',
			'default'
		);
	}

	public function render_product_meta_box( $post ) {
		$status    = get_post_meta( $post->ID, '_caz_woosync_status', true ) ?: 'never';
		$erp_item  = get_post_meta( $post->ID, '_caz_woosync_erp_item', true );
		$last_sync = get_post_meta( $post->ID, '_caz_woosync_last_sync', true );
		$attempts  = (int) get_post_meta( $post->ID, '_caz_woosync_attempt_count', true );

		// Per-product nonce to prevent cross-product replay
		wp_nonce_field( 'caz_woosync_sync_' . $post->ID, 'caz_woosync_product_nonce' );
		?>
		<dl style="margin:0">
			<dt><?php esc_html_e( 'Status', 'caz-woosync' ); ?></dt>
			<dd><?php echo esc_html( ucfirst( $status ) ); ?></dd>

			<?php if ( $erp_item ) : ?>
			<dt><?php esc_html_e( 'ERPNext Item', 'caz-woosync' ); ?></dt>
			<dd><code><?php echo esc_html( $erp_item ); ?></code></dd>
			<?php endif; ?>

			<?php if ( $last_sync ) : ?>
			<dt><?php esc_html_e( 'Last Synced', 'caz-woosync' ); ?></dt>
			<dd><?php echo esc_html( human_time_diff( (int) $last_sync ) . ' ago' ); ?></dd>
			<?php endif; ?>

			<?php if ( $attempts > 0 ) : ?>
			<dt><?php esc_html_e( 'Attempts', 'caz-woosync' ); ?></dt>
			<dd><?php echo esc_html( $attempts ); ?></dd>
			<?php endif; ?>
		</dl>

		<p style="margin-top:10px">
			<button type="button"
				class="button button-secondary caz-force-sync"
				data-product-id="<?php echo esc_attr( $post->ID ); ?>"
				data-action="push"
				style="width:100%;margin-bottom:4px">
				<?php esc_html_e( 'Force Push to ERPNext', 'caz-woosync' ); ?>
			</button>
		</p>
		<?php
	}

	// -------------------------------------------------------------------------
	// Order column — HPOS path (second arg is WC_Order object)
	// -------------------------------------------------------------------------

	public function add_order_erp_column( $columns ) {
		$new = array();
		foreach ( $columns as $key => $label ) {
			$new[ $key ] = $label;
			if ( 'order_number' === $key || 'order_date' === $key ) {
				$new['caz_erp_so'] = __( 'ERPNext SO', 'caz-woosync' );
			}
		}
		return $new;
	}

	/**
	 * HPOS: second arg is WC_Order object.
	 *
	 * @param string   $column Column name.
	 * @param WC_Order $order  Order object.
	 */
	public function render_order_erp_column_hpos( $column, $order ) {
		if ( 'caz_erp_so' !== $column ) {
			return;
		}
		$so = $order->get_meta( '_caz_woosync_erp_so' );
		echo esc_html( $so ?: '—' );
	}

	/**
	 * Legacy: second arg is post ID.
	 *
	 * @param string $column  Column name.
	 * @param int    $post_id Order post ID.
	 */
	public function render_order_erp_column_legacy( $column, $post_id ) {
		if ( 'caz_erp_so' !== $column ) {
			return;
		}
		$order = wc_get_order( $post_id );
		$so    = $order ? $order->get_meta( '_caz_woosync_erp_so' ) : '';
		echo esc_html( $so ?: '—' );
	}

	// -------------------------------------------------------------------------
	// AJAX handlers
	// -------------------------------------------------------------------------

	public function ajax_get_product_status() {
		$product_id = absint( $_POST['product_id'] ?? 0 );
		check_ajax_referer( 'caz_woosync_sync_' . $product_id, 'nonce' );

		if ( ! current_user_can( 'edit_product', $product_id ) ) {
			wp_send_json_error( array( 'message' => __( 'Insufficient permissions.', 'caz-woosync' ) ) );
		}

		wp_send_json_success(
			array(
				'status'        => esc_html( get_post_meta( $product_id, '_caz_woosync_status', true ) ?: 'never' ),
				'erp_item'      => esc_html( get_post_meta( $product_id, '_caz_woosync_erp_item', true ) ),
				'last_sync'     => esc_html( get_post_meta( $product_id, '_caz_woosync_last_sync', true ) ),
				'attempt_count' => (int) get_post_meta( $product_id, '_caz_woosync_attempt_count', true ),
			)
		);
	}

	public function ajax_trigger_sync() {
		$product_id = absint( $_POST['product_id'] ?? 0 );
		check_ajax_referer( 'caz_woosync_sync_' . $product_id, 'nonce' );

		if ( ! current_user_can( 'edit_product', $product_id ) ) {
			wp_send_json_error( array( 'message' => __( 'Insufficient permissions.', 'caz-woosync' ) ) );
		}

		$product = wc_get_product( $product_id );
		if ( ! $product ) {
			wp_send_json_error( array( 'message' => __( 'Product not found.', 'caz-woosync' ) ) );
		}

		$erp_url    = get_option( 'caz_woosync_erp_url', '' );
		$api_key    = get_option( 'caz_woosync_api_key', '' );
		$api_secret = get_option( 'caz_woosync_api_secret', '' );

		if ( ! $erp_url || ! $api_key || ! $api_secret ) {
			wp_send_json_error( array( 'message' => __( 'ERPNext connection not configured. Check CAZ WooSync settings.', 'caz-woosync' ) ) );
		}

		$response = wp_remote_post(
			trailingslashit( $erp_url ) . 'api/method/caz_woosync.api.connection.trigger_item_sync',
			array(
				'headers' => array(
					'Authorization' => 'token ' . $api_key . ':' . $api_secret,
					'Content-Type'  => 'application/json',
				),
				'body'    => wp_json_encode(
					array(
						'store_name'      => get_option( 'caz_woosync_store_name', '' ),
						'woo_product_id'  => (string) $product->get_id(),
					)
				),
				'timeout' => 15,
			)
		);

		if ( is_wp_error( $response ) ) {
			wp_send_json_error( array( 'message' => $response->get_error_message() ) );
		}

		$code = wp_remote_retrieve_response_code( $response );
		if ( 200 === $code ) {
			// Update local meta to show pending
			update_post_meta( $product_id, '_caz_woosync_status', 'pending' );
			wp_send_json_success( array( 'message' => __( 'Sync queued successfully.', 'caz-woosync' ) ) );
		} else {
			wp_send_json_error(
				array( 'message' => sprintf( __( 'ERPNext returned HTTP %d.', 'caz-woosync' ), $code ) )
			);
		}
	}

	// -------------------------------------------------------------------------
	// Admin scripts
	// -------------------------------------------------------------------------

	public function enqueue_admin_scripts( $hook ) {
		$screen = get_current_screen();
		if ( ! $screen ) {
			return;
		}
		// Only load on product edit screen
		if ( 'post.php' !== $hook && 'post-new.php' !== $hook ) {
			return;
		}
		if ( 'product' !== $screen->post_type ) {
			return;
		}
		wp_enqueue_script(
			'caz-woosync-sync-status',
			CAZ_WOOSYNC_PLUGIN_URL . 'admin/js/sync-status.js',
			array( 'jquery' ),
			CAZ_WOOSYNC_VERSION,
			true
		);
		wp_localize_script(
			'caz-woosync-sync-status',
			'cazWooSyncStatus',
			array(
				'ajax_url' => admin_url( 'admin-ajax.php' ),
				'i18n'     => array(
					'syncing' => __( 'Syncing…', 'caz-woosync' ),
					'done'    => __( 'Sync queued.', 'caz-woosync' ),
					'error'   => __( 'Sync failed: ', 'caz-woosync' ),
				),
			)
		);
	}
}
