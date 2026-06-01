jQuery(document).ready(function ($) {
	$(document).on('click', '.caz-force-sync', function () {
		var $btn = $(this);
		var productId = $btn.data('product-id');
		var nonce = $('#caz_woosync_product_nonce').val();

		$btn.prop('disabled', true).text(cazWooSyncStatus.i18n.syncing);

		$.post(cazWooSyncStatus.ajax_url, {
			action: 'caz_woosync_trigger_sync',
			product_id: productId,
			nonce: nonce,
		}, function (res) {
			if (res.success) {
				$btn.text(cazWooSyncStatus.i18n.done);
				setTimeout(function () {
					$btn.prop('disabled', false).text('Force Push to ERPNext');
				}, 3000);
			} else {
				$btn.prop('disabled', false).text(cazWooSyncStatus.i18n.error + (res.data ? res.data.message : ''));
			}
		}).fail(function () {
			$btn.prop('disabled', false).text('Force Push to ERPNext');
		});
	});
});
