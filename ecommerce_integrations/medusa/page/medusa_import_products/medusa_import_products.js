frappe.provide("medusa");

frappe.pages["medusa-import-products"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: "Import Medusa Products",
		single_column: true,
	});
	// eslint-disable-next-line no-undef
	new medusa.ProductImporter(wrapper);
};

const METHOD_BASE =
	"ecommerce_integrations.medusa.page.medusa_import_products.medusa_import_products";

// eslint-disable-next-line no-undef
medusa.ProductImporter = class {
	constructor(wrapper) {
		this.wrapper = $(wrapper).find(".layout-main-section");
		this.page = wrapper.page;
		this.offset = 0;
		this.syncRunning = false;
		this.init();
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
			jobs.find((job) => job.job_name == "medusa.job.sync.all.products") !==
			undefined;

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
                        <h5 class="border-bottom pb-2">Products in Medusa</h5>
                        <div id="medusa-product-list">
                            <div class="text-center">Loading...</div>
                        </div>
                        <div class="medusa-datatable-footer mt-2 pt-3 pb-2 border-top text-right" style="display: none">
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
                            <div id="medusa-sync-info">
                                <div class="py-3 border-bottom">
                                    <button type="button" id="btn-sync-all" class="btn btn-xl btn-primary w-100 font-weight-bold py-3">Sync all Products</button>
                                </div>
                                <div class="product-count py-3 d-flex justify-content-stretch">
                                    <div class="text-center p-3 mx-2 rounded w-100" style="background-color: var(--bg-color)">
                                        <h2 id="count-products-medusa">-</h2>
                                        <p class="text-muted m-0">in Medusa</p>
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
                            <div class="control-value like-disabled-input for-description overflow-auto" id="medusa-sync-log" style="max-height: 500px;"></div>
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
				message: { erpnextCount, medusaCount, syncedCount },
			} = await frappe.call({ method: `${METHOD_BASE}.get_product_count` });

			this.wrapper.find("#count-products-medusa").text(medusaCount);
			this.wrapper.find("#count-products-erpnext").text(erpnextCount);
			this.wrapper.find("#count-products-synced").text(syncedCount);
		} catch (error) {
			frappe.throw(__("Error fetching product count."));
		}
	}

	async addTable() {
		const listElement = this.wrapper.find("#medusa-product-list")[0];
		this.medusaProductTable = new frappe.DataTable(listElement, {
			columns: [
				{ name: "ID", align: "left", editable: false, focusable: false },
				{ name: "Name", editable: false, focusable: false },
				{ name: "SKUs", editable: false, focusable: false },
				{ name: "Status", align: "center", editable: false, focusable: false },
				{ name: "Action", align: "center", editable: false, focusable: false },
			],
			data: await this.fetchMedusaProducts(),
			layout: "fixed",
		});

		this.wrapper.find(".medusa-datatable-footer").show();
	}

	async fetchMedusaProducts(offset = 0) {
		try {
			const {
				message: { products, nextOffset, prevOffset },
			} = await frappe.call({
				method: `${METHOD_BASE}.get_products`,
				args: { offset },
			});
			this.nextOffset = nextOffset;
			this.prevOffset = prevOffset;

			return products.map((product) => ({
				ID: product.id,
				Name: product.title,
				SKUs: product.skus,
				Status: this.getProductSyncStatus(product.synced),
				Action: !product.synced
					? `<button type="button" class="btn btn-default btn-xs btn-sync mx-2" data-product="${product.id}"> Sync </button>`
					: `<button type="button" class="btn btn-default btn-xs btn-resync mx-2" data-product="${product.id}"> Re-sync </button>`,
			}));
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
		this.wrapper.on("click", ".btn-sync, .btn-resync", (e) => {
			const _this = $(e.currentTarget);
			_this.prop("disabled", true).text("Syncing...");

			const product = _this.attr("data-product");
			this.importProduct(product)
				.then((status) => {
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
						`<button type="button" class="btn btn-default btn-xs btn-resync mx-2" data-product="${product}"> Re-sync </button>`,
					);
				})
				.catch(() => {
					_this.prop("disabled", false).text("Sync");
					frappe.throw(__("Error syncing Product"));
				});
		});

		this.wrapper.on("click", ".btn-prev,.btn-next", (e) => this.switchPage(e));
		this.wrapper.on("click", "#btn-sync-all", () => this.syncAll());
	}

	async importProduct(product) {
		const { message: status } = await frappe.call({
			method: `${METHOD_BASE}.import_product`,
			args: { product_id: product },
		});

		if (status) this.fetchProductCount();
		return status;
	}

	async switchPage({ currentTarget }) {
		const _this = $(currentTarget);

		$(".btn-paginate").prop("disabled", true);
		this.medusaProductTable.showToastMessage("Loading...");

		const offset = _this.hasClass("btn-next")
			? this.nextOffset
			: this.prevOffset;

		if (offset !== null && offset !== undefined) {
			this.offset = offset;
			const newProducts = await this.fetchMedusaProducts(offset);
			this.medusaProductTable.refresh(newProducts);
		}

		$(".btn-paginate").prop("disabled", false);
		this.medusaProductTable.clearToastMessage();
	}

	syncAll() {
		this.checkSyncStatus();
		this.toggleSyncAllButton();

		if (this.syncRunning) {
			frappe.msgprint(__("Sync already in progress"));
		} else {
			frappe.call({ method: `${METHOD_BASE}.import_all_products` });
		}

		this.logSync();
	}

	logSync() {
		const _log = $("#medusa-sync-log");
		_log.parents(".card").show();
		_log.text("");

		const _syncedCounter = $("#count-products-synced");
		const _erpnextCounter = $("#count-products-erpnext");

		frappe.realtime.on(
			"medusa.key.sync.all.products",
			({ message, synced, done }) => {
				message = `<pre class="mb-0">${message}</pre>`;
				_log.append(message);
				_log.scrollTop(_log[0].scrollHeight);

				if (synced)
					this.updateSyncedCount(_syncedCounter, _erpnextCounter);

				if (done) {
					frappe.realtime.off("medusa.key.sync.all.products");
					this.toggleSyncAllButton(false);
					this.fetchProductCount();
					this.syncRunning = false;
				}
			},
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
