frappe.provide('shopify');

frappe.pages['shopify-import-products'].on_page_load = function (wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Import Shopify Products',
		single_column: true
	});

	frappe.require([
		'/assets/js/shopify.bundle.js',
		'/assets/js/shopify.product.js'
	], function () {
		new shopify.ProductImporter(wrapper);
	});

}
