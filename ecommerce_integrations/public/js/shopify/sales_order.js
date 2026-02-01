frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.shopify_order_id) {
			frm.add_custom_button(
				__("Fetch Shopify Metafields"),
				function () {
					frappe.call({
						method: "ecommerce_integrations.shopify.order.get_order_metafields",
						args: { sales_order_name: frm.doc.name },
						freeze: true,
						freeze_message: __("Fetching metafields from Shopify..."),
						callback: function (r) {
							if (r.exc) {
								frappe.msgprint({
									title: __("Error"),
									indicator: "red",
									message: r.exc,
								});
								return;
							}
							const data = r.message;
							if (!data.ok) {
								frappe.msgprint({
									title: __("Shopify Metafields"),
									indicator: "orange",
									message: data.message || __("Could not fetch metafields."),
								});
								return;
							}
							show_metafields_modal(data.metafields);
						},
					});
				},
				__("Shopify")
			);
		}
	},
});

function show_metafields_modal(metafields) {
	const title = __("Shopify Order Metafields");
	if (!metafields || metafields.length === 0) {
		frappe.msgprint({
			title: title,
			indicator: "blue",
			message: __("No metafields found for this order."),
		});
		return;
	}

	function esc(s) {
		if (s == null) return "";
		return String(s)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;");
	}
	const rows = metafields.map((m) => {
		const value = m.value != null ? String(m.value) : (m.type === "json" && m.value ? JSON.stringify(m.value) : "—");
		return `<tr>
			<td>${esc(m.namespace)}</td>
			<td>${esc(m.key)}</td>
			<td>${esc(value)}</td>
			<td>${esc(m.type)}</td>
		</tr>`;
	}).join("");

	const html = `
		<div class="shopify-metafields-modal">
			<table class="table table-bordered table-condensed">
				<thead>
					<tr>
						<th>${__("Namespace")}</th>
						<th>${__("Key")}</th>
						<th>${__("Value")}</th>
						<th>${__("Type")}</th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>
		</div>
	`;

	const d = new frappe.ui.Dialog({
		title: title,
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "metafields_html",
				options: html,
			},
		],
	});
	d.show();
}
