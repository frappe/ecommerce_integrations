frappe.provide("shopify");

frappe.pages["shopify-import-products"].on_page_load = function (wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Import Shopify Products",
		single_column: true,
	});
	// eslint-disable-next-line no-undef
	new shopify.ProductImporter(wrapper);
};

// eslint-disable-next-line no-undef
shopify.ProductImporter = class {
	constructor(wrapper) {
		this.wrapper = $(wrapper).find(".layout-main-section");
		this.page = wrapper.page;
		this.init();
		this.syncRunning = false;
	}

	init() {
		frappe.run_serially([
			() => this.addMarkup(),
			() => this.fetchProductCount(),
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
				(job) => job.job_name == "shopify.job.sync.all.products"
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
                        <h5 class="border-bottom pb-2">Products in Shopify</h5>
                        <div id="shopify-product-list">
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
                            <h5 class="border-bottom pb-2">Synchronization Details</h5>
                            <div id="shopify-sync-info">
                                <div class="py-3 border-bottom">
                                    <button type="button" id="btn-sync-all" class="btn btn-xl btn-primary w-100 font-weight-bold py-3">Sync all Products</button>
                                </div>
                                <div class="product-count py-3 d-flex justify-content-stretch">
                                    <div class="text-center p-3 mx-2 rounded w-100" style="background-color: var(--bg-color)">
                                        <h2 id="count-products-shopify">-</h2>
                                        <p class="text-muted m-0">in Shopify</p>
                                    </div>
                                    <div class="text-center p-3 mx-2 rounded w-100" style="background-color: var(--bg-color)">
                                        <h2 id="count-products-erpnext">-</h2>
                                        <p class="text-muted m-0">in ERPNext</p>
                                    </div>
                                    <div class="text-center p-3 mx-2 rounded w-100" style="background-color: var(--bg-color)">
                                        <h2 id="count-products-synced">-</h2>
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

	async fetchProductCount() {
		try {
			const {
				message: { erpnextCount, shopifyCount, syncedCount },
			} = await frappe.call({
				method: "ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.get_product_count",
			});

			this.wrapper.find("#count-products-shopify").text(shopifyCount);
			this.wrapper.find("#count-products-erpnext").text(erpnextCount);
			this.wrapper.find("#count-products-synced").text(syncedCount);
		} catch (error) {
			frappe.throw(__("Error fetching product count."));
		}
	}

	async addTable() {
		const listElement = this.wrapper.find("#shopify-product-list")[0];
		this.shopifyProductTable = new frappe.DataTable(listElement, {
			columns: [
				// {
				//     name: 'Image',
				//     align: 'center',
				// },
				{
					name: "ID",
					align: "left",
					editable: false,
					focusable: false,
				},
				{
					name: "Name",
					editable: false,
					focusable: false,
				},
				{
					name: "SKUs",
					editable: false,
					focusable: false,
				},
				{
					name: "Status",
					align: "center",
					editable: false,
					focusable: false,
				},
				{
					name: "Action",
					align: "center",
					editable: false,
					focusable: false,
				},
			],
			data: await this.fetchShopifyProducts(),
			layout: "fixed",
		});

		this.wrapper.find(".shopify-datatable-footer").show();
	}

	async fetchShopifyProducts(from_ = null) {
		try {
			const {
				message: { products, nextUrl, prevUrl },
			} = await frappe.call({
				method: "ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.get_shopify_products",
				args: { from_ },
			});
			this.nextUrl = nextUrl;
			this.prevUrl = prevUrl;

			const shopifyProducts = products.map((product) => ({
				// 'Image': product.image && product.image.src && `<img style="height: 50px" src="${product.image.src}">`,
				ID: product.id,
				Name: product.title,
				SKUs:
					product.variants &&
					product.variants.map((a) => `${a.sku}`).join(", "),
				Status: this.getProductSyncStatus(product.synced),
				Action: !product.synced
					? `<button type="button" class="btn btn-default btn-xs btn-sync mx-2" data-product="${product.id}"> Sync </button>`
					: `<button type="button" class="btn btn-default btn-xs btn-resync mx-2" data-product="${product.id}"> Re-sync </button>`,
			}));

			return shopifyProducts;
		} catch (error) {
			frappe.throw(__("Error fetching products."));
		}
	}

	getProductSyncStatus(status) {
		return status
			? `<span class="indicator-pill green">Synced</span>`
			: `<span class="indicator-pill orange">Not Synced</span>`;
	}

	listen() {
		// sync a product from table
		this.wrapper.on("click", ".btn-sync", (e) => {
			const _this = $(e.currentTarget);

			_this.prop("disabled", true).text("Syncing...");

			const product = _this.attr("data-product");
			this.syncProduct(product).then((status) => {
				if (!status) {
					frappe.throw(__("Error syncing product"));
					_this.prop("disabled", false).text("Sync");
					return;
				}

				_this
					.parents(".dt-row")
					.find(".indicator-pill")
					.replaceWith(this.getProductSyncStatus(true));

				_this.replaceWith(
					`<button type="button" class="btn btn-default btn-xs btn-resync mx-2" data-product="${product}"> Re-sync </button>`
				);
			});
		});

		this.wrapper.on("click", ".btn-resync", (e) => {
			const _this = $(e.currentTarget);

			_this.prop("disabled", true).text("Syncing...");

			const product = _this.attr("data-product");
			this.resyncProduct(product)
				.then((status) => {
					if (!status) {
						frappe.throw(__("Error syncing product"));
						return;
					}

					_this
						.parents(".dt-row")
						.find(".indicator-pill")
						.replaceWith(this.getProductSyncStatus(true));

					_this.prop("disabled", false).text("Re-sync");
				})
				.catch((ex) => {
					_this.prop("disabled", false).text("Re-sync");
					frappe.throw(__("Error syncing Product"));
				});
		});

		// pagination
		this.wrapper.on("click", ".btn-prev,.btn-next", (e) =>
			this.switchPage(e)
		);

		// sync all products
		this.wrapper.on("click", "#btn-sync-all", (e) => this.syncAll(e));
	}

	async syncProduct(product) {
		const { message: status } = await frappe.call({
			method: "ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.sync_product",
			args: { product },
		});

		if (status) this.fetchProductCount();

		return status;
	}

	async resyncProduct(product) {
		const { message: status } = await frappe.call({
			method: "ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.resync_product",
			args: { product },
		});

		if (status) this.fetchProductCount();

		return status;
	}

	async switchPage({ currentTarget }) {
		const _this = $(currentTarget);

		$(".btn-paginate").prop("disabled", true);
		this.shopifyProductTable.showToastMessage("Loading...");

		const newProducts = await this.fetchShopifyProducts(
			_this.hasClass("btn-next") ? this.nextUrl : this.prevUrl
		);

		this.shopifyProductTable.refresh(newProducts);

		$(".btn-paginate").prop("disabled", false);
		this.shopifyProductTable.clearToastMessage();
	}

	syncAll() {
		this.checkSyncStatus();
		this.toggleSyncAllButton();

		if (this.syncRunning) {
			frappe.msgprint(__("Sync already in progress"));
		} else {
			frappe.call({
				method: "ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.import_all_products",
			});
		}

		// sync progress
		this.logSync();
	}

	logSync() {
		const _log = $("#shopify-sync-log");
		_log.parents(".card").show();
		_log.text(""); // clear logs

		// define counters here to prevent calling jquery every time
		const _syncedCounter = $("#count-products-synced");
		const _erpnextCounter = $("#count-products-erpnext");

		frappe.realtime.on(
			"shopify.key.sync.all.products",
			({ message, synced, done, error }) => {
				message = `<pre class="mb-0">${message}</pre>`;
				_log.append(message);
				_log.scrollTop(_log[0].scrollHeight);

				if (synced)
					this.updateSyncedCount(_syncedCounter, _erpnextCounter);

				if (done) {
					frappe.realtime.off("shopify.key.sync.all.products");
					this.toggleSyncAllButton(false);
					this.fetchProductCount();
					this.syncRunning = false;
				}
			}
		);
	}

	toggleSyncAllButton(disable = true) {
		const btn = $("#btn-sync-all");

		const _toggleClass = (d) => (d ? "btn-success" : "btn-primary");
		const _toggleText = () => (disable ? "Syncing..." : "Sync Products");

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
