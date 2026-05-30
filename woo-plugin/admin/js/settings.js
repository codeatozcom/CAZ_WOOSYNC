jQuery(document).ready(function ($) {
	var $btn = $('<button type="button" class="button button-secondary" style="margin-top:10px">Test Connection to ERPNext</button>');
	var $result = $('<p class="description" style="margin-top:8px"></p>');
	$('#caz_woosync_api_secret').closest('tr').after(
		$('<tr><th></th><td></td></tr>').find('td').append($btn).append($result).end()
	);

	$btn.on('click', function () {
		$btn.prop('disabled', true).text(cazWooSync.i18n.testing);
		$result.text('').css('color', '');
		$.post(cazWooSync.ajax_url, {
			action: 'caz_woosync_test_connection',
			nonce: cazWooSync.nonce,
		}, function (res) {
			if (res.success) {
				$result.text('✅ ' + res.data.message).css('color', 'green');
			} else {
				$result.text('❌ ' + cazWooSync.i18n.failed + res.data.message).css('color', 'red');
			}
		}).fail(function () {
			$result.text('❌ Request failed. Check browser console.').css('color', 'red');
		}).always(function () {
			$btn.prop('disabled', false).text('Test Connection to ERPNext');
		});
	});

	// Install Webhooks button
	$('#caz-install-webhooks').on('click', function () {
		var $wbtn = $(this);
		var $wresult = $('#caz-webhook-result');
		$wbtn.prop('disabled', true).text(cazWooSync.i18n.installing);
		$wresult.text('').css('color', '');
		$.post(cazWooSync.ajax_url, {
			action: 'caz_woosync_install_webhooks',
			nonce: cazWooSync.webhook_nonce,
		}, function (res) {
			if (res.success) {
				$wresult.text('✅ ' + res.data.message).css('color', 'green');
			} else {
				$wresult.text('❌ ' + cazWooSync.i18n.webhooks_failed + res.data.message).css('color', 'red');
			}
		}).fail(function () {
			$wresult.text('❌ Request failed.').css('color', 'red');
		}).always(function () {
			$wbtn.prop('disabled', false).text('Install Webhooks');
		});
	});
});
