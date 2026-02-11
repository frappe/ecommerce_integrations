frappe.provide("shopify");

frappe.pages["shopify-import-orders"].on_page_load = function (wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Import Shopify Orders",
		single_column: true,
	});
	new shopify.OrderImporter(wrapper);
};

shopify.OrderImporter = class {
	constructor(wrapper) {
		this.wrapper = $(wrapper).find(".layout-main-section");
		this.page = wrapper.page;
		this.init();
		this.syncRunning = false;
	}

	init() {
		frappe.run_serially([
			() => this.addMarkup(),
			() => this.setupDateFilters(),
			() => this.fetchOrderCount(),
			() => this.addTable(),
			() => this.checkSyncStatus(),
			() => this.listen(),
		]);
	}

	async checkSyncStatus() {
		const jobs = await frappe.db.get_list("RQ Job", {
			filters: { status: ("in", ("queued", "started")) },
		});
		this.syncRunning =
			jobs.find(
				(job) => job.job_name === "shopify.job.sync.all.orders",
			) !== undefined;

		if (this.syncRunning) {
			this.toggleSyncAllButton();
			this.logSync();
		}
	}

	addMarkup() {
		const _markup = $(`
			<div class="row">
				<div class="col-lg-8 d-flex align-items-stretch">
					<div class="card border-0 shadow-sm p-3 mb-3 w-100 rounded-sm" style="background-color: var(--card-bg)">
						<h5 class="border-bottom pb-2">Orders in Shopify</h5>
						<div id="shopify-order-list">
							<div class="text-center">Loading...</div>
						</div>
						<div class="shopify-datatable-footer mt-2 pt-3 pb-2 border-top text-right" style="display: none">
							<div class="btn-group">
								<button type="button" class="btn btn-sm btn-default btn-paginate btn-prev">Prev</button>
								<button type="button" class="btn btn-sm btn-default btn-paginate btn-next">Next</button>
							</div>
						</div>
					</div>
				</div>
				<div class="col-lg-4 d-flex align-items-stretch">
					<div class="w-100">
						<div class="card border-0 shadow-sm p-3 mb-3 rounded-sm" style="background-color: var(--card-bg)">
							<h5 class="border-bottom pb-2">Date Range Filter</h5>
							<div class="row">
								<div class="col-6" id="filter-from-date"></div>
								<div class="col-6" id="filter-to-date"></div>
							</div>
							<button type="button" id="btn-fetch-orders" class="btn btn-sm btn-default w-100 mt-2">Fetch Orders</button>
						</div>

						<div class="card border-0 shadow-sm p-3 mb-3 rounded-sm" style="background-color: var(--card-bg)">
							<h5 class="border-bottom pb-2">Synchronization Details</h5>
							<div id="shopify-sync-info">
								<div class="py-3 border-bottom">
									<button type="button" id="btn-sync-all" class="btn btn-xl btn-primary w-100 font-weight-bold py-3">Sync all Orders</button>
								</div>
								<div class="order-count py-3 d-flex justify-content-stretch">
									<div class="text-center p-3 mx-2 rounded w-100" style="background-color: var(--bg-color)">
										<h2 id="count-orders-shopify">-</h2>
										<p class="text-muted m-0">in Shopify</p>
									</div>
									<div class="text-center p-3 mx-2 rounded w-100" style="background-color: var(--bg-color)">
										<h2 id="count-orders-erpnext">-</h2>
										<p class="text-muted m-0">in ERPNext</p>
									</div>
									<div class="text-center p-3 mx-2 rounded w-100" style="background-color: var(--bg-color)">
										<h2 id="count-orders-synced">-</h2>
										<p class="text-muted m-0">Synced</p>
									</div>
								</div>
							</div>
						</div>

						<div class="card border-0 shadow-sm p-3 mb-3 rounded-sm" style="background-color: var(--card-bg); display: none;">
							<h5 class="border-bottom pb-2">Sync Log</h5>
							<div class="control-value like-disabled-input for-description overflow-auto" id="shopify-sync-log" style="max-height: 500px;"></div>
						</div>

					</div>
				</div>
			</div>
		`);

		this.wrapper.append(_markup);
	}

	setupDateFilters() {
		this.fromDateControl = frappe.ui.form.make_control({
			df: {
				fieldtype: "Date",
				fieldname: "from_date",
				label: "From Date",
			},
			parent: this.wrapper.find("#filter-from-date"),
			render_input: true,
		});

		this.toDateControl = frappe.ui.form.make_control({
			df: {
				fieldtype: "Date",
				fieldname: "to_date",
				label: "To Date",
			},
			parent: this.wrapper.find("#filter-to-date"),
			render_input: true,
		});
	}

	async fetchOrderCount() {
		try {
			const {
				message: { erpnextCount, shopifyCount, syncedCount },
			} = await frappe.call({
				method: "ecommerce_integrations.shopify.page.shopify_import_orders.shopify_import_orders.get_order_count",
			});

			this.wrapper.find("#count-orders-shopify").text(shopifyCount);
			this.wrapper.find("#count-orders-erpnext").text(erpnextCount);
			this.wrapper.find("#count-orders-synced").text(syncedCount);
		} catch (error) {
			frappe.throw(__("Error fetching order count."));
		}
	}

	async addTable() {
		const listElement = this.wrapper.find("#shopify-order-list")[0];
		this.shopifyOrderTable = new frappe.DataTable(listElement, {
			columns: [
				{
					name: "Order #",
					align: "left",
					editable: false,
					focusable: false,
					width: 90,
				},
				{
					name: "ID",
					align: "left",
					editable: false,
					focusable: false,
					width: 120,
				},
				{
					name: "Customer",
					editable: false,
					focusable: false,
					width: 140,
				},
				{
					name: "Financial",
					align: "center",
					editable: false,
					focusable: false,
					width: 100,
				},
				{
					name: "Fulfillment",
					align: "center",
					editable: false,
					focusable: false,
					width: 110,
				},
				{
					name: "Total",
					align: "right",
					editable: false,
					focusable: false,
					width: 90,
				},
				{
					name: "Date",
					align: "left",
					editable: false,
					focusable: false,
					width: 100,
				},
				{
					name: "Status",
					align: "center",
					editable: false,
					focusable: false,
					width: 90,
				},
				{
					name: "Action",
					align: "center",
					editable: false,
					focusable: false,
					width: 80,
				},
			],
			data: await this.fetchShopifyOrders(),
			layout: "fixed",
		});

		this.wrapper.find(".shopify-datatable-footer").show();
	}

	async fetchShopifyOrders(from_ = null) {
		try {
			const args = { from_ };
			if (!from_) {
				const fromDate = this.fromDateControl && this.fromDateControl.get_value();
				const toDate = this.toDateControl && this.toDateControl.get_value();
				if (fromDate) args.created_at_min = fromDate;
				if (toDate) args.created_at_max = toDate;
			}

			const {
				message: { orders, nextUrl, prevUrl },
			} = await frappe.call({
				method: "ecommerce_integrations.shopify.page.shopify_import_orders.shopify_import_orders.get_shopify_orders",
				args: args,
			});
			this.nextUrl = nextUrl;
			this.prevUrl = prevUrl;

			const shopifyOrders = orders.map((order) => ({
				"Order #": order.name || "",
				ID: order.id,
				Customer: this.getCustomerName(order),
				Financial: this.getFinancialPill(order.financial_status),
				Fulfillment: this.getFulfillmentPill(order.fulfillment_status),
				Total: `${order.currency || ""} ${order.total_price || "0.00"}`,
				Date: order.created_at ? frappe.datetime.str_to_user(order.created_at.split("T")[0]) : "",
				Status: this.getOrderSyncStatus(order.synced),
				Action: !order.synced
					? `<button type="button" class="btn btn-default btn-xs btn-sync mx-2" data-order="${order.id}">Sync</button>`
					: `<span class="text-muted text-small">Synced</span>`,
			}));

			return shopifyOrders;
		} catch (error) {
			frappe.throw(__("Error fetching orders."));
		}
	}

	getCustomerName(order) {
		if (order.customer) {
			const first = order.customer.first_name || "";
			const last = order.customer.last_name || "";
			return (first + " " + last).trim() || "Customer";
		}
		return "Guest";
	}

	getFinancialPill(status) {
		if (!status) return "";
		const colors = {
			paid: "green",
			partially_paid: "orange",
			pending: "orange",
			authorized: "blue",
			refunded: "red",
			partially_refunded: "orange",
			voided: "red",
		};
		const color = colors[status] || "grey";
		return `<span class="indicator-pill ${color}">${status}</span>`;
	}

	getFulfillmentPill(status) {
		if (!status) return `<span class="indicator-pill orange">unfulfilled</span>`;
		const colors = {
			fulfilled: "green",
			partial: "orange",
			restocked: "blue",
		};
		const color = colors[status] || "grey";
		return `<span class="indicator-pill ${color}">${status}</span>`;
	}

	getOrderSyncStatus(status) {
		return status
			? `<span class="indicator-pill green">Synced</span>`
			: `<span class="indicator-pill orange">Not Synced</span>`;
	}

	listen() {
		// sync an order from table
		this.wrapper.on("click", ".btn-sync", (e) => {
			const _this = $(e.currentTarget);

			_this.prop("disabled", true).text("Queuing...");

			const orderId = _this.attr("data-order");
			this.syncOrder(orderId).then((status) => {
				if (!status) {
					frappe.msgprint(__("Error queuing order sync. Check the Ecommerce Integration Log for details."));
					_this.prop("disabled", false).text("Sync");
					return;
				}

				_this.replaceWith(`<span class="indicator-pill blue">Queued</span>`);
				frappe.show_alert({message: __("Order sync queued. Refresh page to see updated status."), indicator: "blue"});
			});
		});

		// pagination
		this.wrapper.on("click", ".btn-prev,.btn-next", (e) =>
			this.switchPage(e),
		);

		// fetch orders with date filter
		this.wrapper.on("click", "#btn-fetch-orders", () => this.refetchOrders());

		// sync all orders
		this.wrapper.on("click", "#btn-sync-all", () => this.syncAll());
	}

	async syncOrder(orderId) {
		const { message: status } = await frappe.call({
			method: "ecommerce_integrations.shopify.page.shopify_import_orders.shopify_import_orders.sync_order",
			args: { order_id: orderId },
		});

		if (status) this.fetchOrderCount();

		return status;
	}

	async switchPage({ currentTarget }) {
		const _this = $(currentTarget);

		$(".btn-paginate").prop("disabled", true);
		this.shopifyOrderTable.showToastMessage("Loading...");

		const newOrders = await this.fetchShopifyOrders(
			_this.hasClass("btn-next") ? this.nextUrl : this.prevUrl,
		);

		this.shopifyOrderTable.refresh(newOrders);

		$(".btn-paginate").prop("disabled", false);
		this.shopifyOrderTable.clearToastMessage();
	}

	async refetchOrders() {
		this.shopifyOrderTable.showToastMessage("Loading...");
		const newOrders = await this.fetchShopifyOrders();
		this.shopifyOrderTable.refresh(newOrders);
		this.shopifyOrderTable.clearToastMessage();
	}

	syncAll() {
		this.checkSyncStatus();
		this.toggleSyncAllButton();

		if (this.syncRunning) {
			frappe.msgprint(__("Sync already in progress"));
		} else {
			const args = {};
			const fromDate = this.fromDateControl && this.fromDateControl.get_value();
			const toDate = this.toDateControl && this.toDateControl.get_value();
			if (fromDate) args.created_at_min = fromDate;
			if (toDate) args.created_at_max = toDate;

			frappe.call({
				method: "ecommerce_integrations.shopify.page.shopify_import_orders.shopify_import_orders.import_all_orders",
				args: args,
			});
		}

		// sync progress
		this.logSync();
	}

	logSync() {
		const _log = $("#shopify-sync-log");
		_log.parents(".card").show();
		_log.text(""); // clear logs

		const _syncedCounter = $("#count-orders-synced");
		const _erpnextCounter = $("#count-orders-erpnext");

		frappe.realtime.on(
			"shopify.key.sync.all.orders",
			({ message, synced, done, error }) => {
				message = `<pre class="mb-0">${message}</pre>`;
				_log.append(message);
				_log.scrollTop(_log[0].scrollHeight);

				if (synced)
					this.updateSyncedCount(_syncedCounter, _erpnextCounter);

				if (done) {
					frappe.realtime.off("shopify.key.sync.all.orders");
					this.toggleSyncAllButton(false);
					this.fetchOrderCount();
					this.syncRunning = false;
				}
			},
		);
	}

	toggleSyncAllButton(disable = true) {
		const btn = $("#btn-sync-all");

		const _toggleClass = (d) => (d ? "btn-success" : "btn-primary");
		const _toggleText = () => (disable ? "Syncing..." : "Sync all Orders");

		btn.prop("disabled", disable)
			.addClass(_toggleClass(disable))
			.removeClass(_toggleClass(!disable))
			.text(_toggleText());
	}

	updateSyncedCount(_syncedCounter, _erpnextCounter) {
		let _synced = parseFloat(_syncedCounter.text());
		let _erpnext = parseFloat(_erpnextCounter.text());

		_syncedCounter.text(_synced + 1);
		_erpnextCounter.text(_erpnext + 1);
	}
};
