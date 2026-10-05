frappe.provide('shopify');

frappe.pages['shopify-import-products'].on_page_load = function (wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Import Shopify Products',
		single_column: true
	});

	new shopify.ProductImporter(wrapper);

}

shopify.ProductImporter = class {


	constructor(wrapper) {

		this.wrapper = $(wrapper).find('.layout-main-section');
		this.page = wrapper.page;

		this.productSearch = '';
		this.allShopifyProducts = [];

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
		const jobs = await frappe.db.get_list("RQ Job", {filters: {"status": ("in", ("queued", "started"))}});
		this.syncRunning = jobs.find(job => job.job_name == 'shopify.job.sync.all.products') !== undefined;

		if (this.syncRunning) {
			this.toggleSyncAllButton();
			this.logSync();
		}

	}

	addMarkup() {

		const _markup = $(`
			<div class="row">

				<!-- Products in Shopify -->
				<div class="col-lg-8 d-flex align-items-stretch">

					<div class="card border-0 shadow-sm p-3 mb-3 w-100 rounded-sm"
						style="background-color: var(--card-bg)">

						<h5 class="border-bottom pb-2 mb-3">
							Products in Shopify
						</h5>

						<!-- Search -->
						<div class="mb-3">
							<input
								type="text"
								class="form-control"
								id="shopify-product-search"
								placeholder="${__('Search product name...')}"
							>
						</div>

						<!-- Product Table -->
						<div id="shopify-product-list">
							<div class="text-center">
								Loading...
							</div>
						</div>

						<!-- Pagination -->
						<div
							class="shopify-datatable-footer mt-2 pt-3 pb-2 border-top text-right"
							style="display: none"
						>
							<div class="btn-group">

								<button
									type="button"
									class="btn btn-sm btn-default btn-paginate btn-prev"
								>
									${__('Prev')}
								</button>

								<button
									type="button"
									class="btn btn-sm btn-default btn-paginate btn-next"
								>
									${__('Next')}
								</button>

							</div>
						</div>

					</div>

				</div>


				<!-- Synchronization Details -->
				<div class="col-lg-4 d-flex align-items-stretch">

					<div class="w-100">

						<div
							class="card border-0 shadow-sm p-3 mb-3 rounded-sm"
							style="background-color: var(--card-bg)"
						>

							<h5 class="border-bottom pb-2">
								Synchronization Details
							</h5>

							<div id="shopify-sync-info">

								<div class="py-3 border-bottom">

									<button
										type="button"
										id="btn-sync-all"
										class="btn btn-xl btn-primary w-100 font-weight-bold py-3"
									>
										Sync all Products
									</button>

								</div>

								<div class="product-count py-3 d-flex justify-content-stretch">

									<div
										class="text-center p-3 mx-2 rounded w-100"
										style="background-color: var(--bg-color)"
									>
										<h2 id="count-products-shopify">-</h2>
										<p class="text-muted m-0">
											in Shopify
										</p>
									</div>

									<div
										class="text-center p-3 mx-2 rounded w-100"
										style="background-color: var(--bg-color)"
									>
										<h2 id="count-products-erpnext">-</h2>
										<p class="text-muted m-0">
											in ERPNext
										</p>
									</div>

									<div
										class="text-center p-3 mx-2 rounded w-100"
										style="background-color: var(--bg-color)"
									>
										<h2 id="count-products-synced">-</h2>
										<p class="text-muted m-0">
											Synced
										</p>
									</div>

								</div>

							</div>

						</div>


						<!-- Sync Log -->
						<div
							class="card border-0 shadow-sm p-3 mb-3 rounded-sm"
							style="background-color: var(--card-bg); display: none;"
						>

							<h5 class="border-bottom pb-2">
								Sync Log
							</h5>

							<div
								class="control-value like-disabled-input for-description overflow-auto"
								id="shopify-sync-log"
								style="max-height: 500px;"
							></div>

						</div>

					</div>

				</div>

			</div>
		`);

		this.wrapper.append(_markup);
	}

	// async openMappingDialog(productId) {

	// 		const product =
	// 			this.allShopifyProducts.find(
	// 				p =>
	// 					String(p.id) ===
	// 					String(productId)
	// 			);

	// 		if (!product) {

	// 			frappe.msgprint(
	// 				__('Shopify product not found.')
	// 			);

	// 			return;
	// 		}

	// 		// ---------------------------------------------------------
	// 		// Load existing mappings
	// 		// ---------------------------------------------------------

	// 		const {
	// 			message: existingMappings
	// 		} = await frappe.call({

	// 			method:
	// 				'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.get_shopify_mapping',

	// 			args: {
	// 				product_id: productId
	// 			}
	// 		});

	// 		const mappings =
	// 			existingMappings || [];

	// 		const variants =
	// 			product.variants || [];

	// 		// ---------------------------------------------------------
	// 		// Determine product type
	// 		// ---------------------------------------------------------

	// 		const hasVariants =
	// 			variants.length > 1 ||
	// 			(
	// 				variants.length === 1 &&
	// 				variants[0].title &&
	// 				variants[0].title.toLowerCase() !==
	// 					'default title'
	// 			);

	// 		let dialog;

	// 		const fields = [];

	// 		// =========================================================
	// 		// SIMPLE SHOPIFY PRODUCT
	// 		// =========================================================

	// 		if (!hasVariants) {

	// 			const variant =
	// 				variants[0];

	// 			const existing =
	// 				mappings.find(
	// 					row =>
	// 						String(row.variant_id || '') ===
	// 						String(variant?.id || '')
	// 				) || mappings[0];

	// 			fields.push({

	// 				fieldname:
	// 					'erp_item_code',

	// 				label:
	// 					__('ERPNext Item'),

	// 				fieldtype:
	// 					'Link',

	// 				options:
	// 					'Item',

	// 				reqd:
	// 					1,

	// 				default:
	// 					existing?.erpnext_item_code || '',

	// 				get_query() {

	// 					return {

	// 						filters: {

	// 							disabled: 0,

	// 							is_sales_item: 1,

	// 							has_variants: 0

	// 						}

	// 					};

	// 				}

	// 			});

	// 		}

	// 		// =========================================================
	// 		// SHOPIFY PRODUCT WITH VARIANTS
	// 		// =========================================================

	// 		else {

	// 			const existingTemplate =
	// 				mappings.find(
	// 					row =>
	// 						Number(row.has_variants) === 1
	// 				);

	// 			// -----------------------------------------------------
	// 			// ERP Template
	// 			// -----------------------------------------------------

	// 			fields.push({

	// 				fieldname:
	// 					'template_item',

	// 				label:
	// 					__('ERPNext Template'),

	// 				fieldtype:
	// 					'Link',

	// 				options:
	// 					'Item',

	// 				reqd:
	// 					0,

	// 				default:
	// 					existingTemplate
	// 						?.erpnext_item_code || '',

	// 				get_query() {

	// 					return {

	// 						filters: {

	// 							disabled: 0,

	// 							is_sales_item: 1,

	// 							has_variants: 1

	// 						}

	// 					};

	// 				}

	// 			});

	// 			fields.push({

	// 				fieldtype:
	// 					'Section Break',

	// 				label:
	// 					__('Variant Mapping')

	// 			});

	// 			// -----------------------------------------------------
	// 			// Shopify variants
	// 			// -----------------------------------------------------

	// 			variants.forEach(
	// 				variant => {

	// 					const existingVariant =
	// 						mappings.find(
	// 							row =>
	// 								String(
	// 									row.variant_id || ''
	// 								) ===
	// 								String(
	// 									variant.id
	// 								)
	// 						);

	// 					fields.push({

	// 						fieldname:
	// 							`variant_${variant.id}`,

	// 						label:
	// 							`${variant.title || 'Default Title'}` +
	// 							(
	// 								variant.sku
	// 									? ` (${variant.sku})`
	// 									: ''
	// 							),

	// 						fieldtype:
	// 							'Link',

	// 						options:
	// 							'Item',

	// 						reqd:
	// 							1,

	// 						default:
	// 							existingVariant
	// 								?.erpnext_item_code || '',

	// 						get_query() {

	// 							const template =
	// 								dialog?.get_value(
	// 									'template_item'
	// 								);

	// 							if (!template) {

	// 								return {

	// 									filters: {

	// 										disabled: 0,

	// 										is_sales_item: 1,

	// 										has_variants: 0

	// 									}

	// 								};

	// 							}

	// 							return {

	// 								filters: {

	// 									disabled: 0,

	// 									is_sales_item: 1,

	// 									has_variants: 0,

	// 									variant_of:
	// 										template

	// 								}

	// 							};

	// 						}

	// 					});

	// 				}
	// 			);

	// 		}

	// 		// =========================================================
	// 		// DIALOG
	// 		// =========================================================

	// 		dialog = new frappe.ui.Dialog({

	// 			title:
	// 				__('Map Shopify Product'),

	// 			fields:
	// 				fields,

	// 			size:
	// 				hasVariants
	// 					? 'large'
	// 					: 'small',

	// 			primary_action_label:
	// 				__('Save Mapping'),

	// 			primary_action:
	// 				async values => {

	// 					try {

	// 						const mappingData = [];

	// 						// =========================================
	// 						// SIMPLE PRODUCT
	// 						// =========================================

	// 						if (!hasVariants) {

	// 							const variant =
	// 								variants[0];

	// 							mappingData.push({

	// 								erp_item_code:
	// 									values.erp_item_code,

	// 								variant_id:
	// 									String(
	// 										variant.id
	// 									),

	// 								sku:
	// 									variant.sku || '',

	// 								has_variants:
	// 									0,

	// 								variant_of:
	// 									''

	// 							});

	// 						}

	// 						// =========================================
	// 						// VARIANT PRODUCT
	// 						// =========================================

	// 						else {

	// 							// -------------------------------
	// 							// Template mapping
	// 							// -------------------------------

	// 							mappingData.push({

	// 								erp_item_code:
	// 									values.template_item,

	// 								variant_id:
	// 									'',

	// 								sku:
	// 									'',

	// 								has_variants:
	// 									1,

	// 								variant_of:
	// 									''

	// 							});

	// 							// -------------------------------
	// 							// Variant mappings
	// 							// -------------------------------

	// 							variants.forEach(
	// 								variant => {

	// 									mappingData.push({

	// 										erp_item_code:
	// 											values[
	// 												`variant_${variant.id}`
	// 											],

	// 										variant_id:
	// 											String(
	// 												variant.id
	// 											),

	// 										sku:
	// 											variant.sku || '',

	// 										has_variants:
	// 											0,

	// 										variant_of:
	// 											values.template_item

	// 									});

	// 								}
	// 							);

	// 						}

	// 						// =========================================
	// 						// SAVE
	// 						// =========================================

	// 						const primaryButton =
	// 							dialog.get_primary_btn();

	// 						primaryButton
	// 							.prop(
	// 								'disabled',
	// 								true
	// 							)
	// 							.text(
	// 								__('Saving...')
	// 							);

	// 						const {
	// 							message
	// 						} = await frappe.call({

	// 							method:
	// 								'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.map_shopify_product',

	// 							args: {

	// 								product_id:
	// 									productId,

	// 								mappings:
	// 									JSON.stringify(
	// 										mappingData
	// 									)

	// 							}

	// 						});

	// 						if (!message?.success) {

	// 							frappe.throw(
	// 								__(
	// 									'Unable to save Shopify mapping.'
	// 								)
	// 							);

	// 						}

	// 						// =========================================
	// 						// SUCCESS
	// 						// =========================================

	// 						dialog.hide();

	// 						frappe.show_alert({

	// 							message:
	// 								__(
	// 									'Shopify product mapping saved successfully.'
	// 								),

	// 							indicator:
	// 								'green'

	// 						});

	// 						// Update local product
	// 						product.synced = true;

	// 						product.erp_item_code =
	// 							hasVariants
	// 								? values.template_item
	// 								: values.erp_item_code;

	// 						// Refresh table
	// 						const search =
	// 							this.productSearch || '';

	// 						const filteredProducts =
	// 							this.allShopifyProducts.filter(
	// 								p => {

	// 									const name =
	// 										(
	// 											p.title || ''
	// 										).toLowerCase();

	// 									return name.includes(
	// 										search
	// 									);

	// 								}
	// 							);

	// 						this.shopifyProductTable.refresh(
	// 							this.formatShopifyProducts(
	// 								filteredProducts
	// 							)
	// 						);

	// 						this.fetchProductCount();

	// 					}

	// 					catch (error) {

	// 						console.error(
	// 							error
	// 						);

	// 						dialog
	// 							.get_primary_btn()
	// 							.prop(
	// 								'disabled',
	// 								false
	// 							)
	// 							.text(
	// 								__('Save Mapping')
	// 							);

	// 						frappe.throw(
	// 							__(
	// 								'Error saving Shopify mapping.'
	// 							)
	// 						);

	// 					}

	// 				}

	// 		});

	// 		dialog.show();
	// 	}

	async openMappingDialog(productId) {

		const product =
			this.allShopifyProducts.find(
				p =>
					String(p.id) ===
					String(productId)
			);

		if (!product) {

			frappe.msgprint(
				__('Shopify product not found.')
			);

			return;
		}

		// ---------------------------------------------------------
		// Load existing mappings
		// ---------------------------------------------------------

		const {
			message: existingMappings
		} = await frappe.call({

			method:
				'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.get_shopify_mapping',

			args: {
				product_id: productId
			}
		});

		const mappings =
			existingMappings || [];

		const variants =
			product.variants || [];

		// ---------------------------------------------------------
		// Determine product type
		// ---------------------------------------------------------

		const hasVariants =
			variants.length > 1 ||
			(
				variants.length === 1 &&
				variants[0].title &&
				variants[0].title.toLowerCase() !==
					'default title'
			);

		// ---------------------------------------------------------
		// Get Shopify Setting
		// ---------------------------------------------------------

		const {
			message: allowIndividualItem
		} = await frappe.call({

			method:
				'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.get_variant_mapping_setting'

		});

		const allowIndividualItemMapping =
			cint(allowIndividualItem);

		console.log(
			"Allow Individual Item Mapping:",
			allowIndividualItemMapping
		);

		let dialog;

		const fields = [];

		// =========================================================
		// SIMPLE SHOPIFY PRODUCT
		// =========================================================

		if (!hasVariants) {

			const variant =
				variants[0];

			const existing =
				mappings.find(
					row =>
						String(row.variant_id || '') ===
						String(variant?.id || '')
				) || mappings[0];

			fields.push({

				fieldname:
					'erp_item_code',

				label:
					__('ERPNext Item'),

				fieldtype:
					'Link',

				options:
					'Item',

				reqd:
					1,

				default:
					existing?.erpnext_item_code || '',

				get_query() {

					return {

						filters: {

							disabled: 0,

							is_sales_item: 1,

							has_variants: 0

						}

					};

				}

			});

		}

		// =========================================================
		// SHOPIFY PRODUCT WITH VARIANTS
		// =========================================================

		else {

			// -----------------------------------------------------
			// Existing Template Mapping
			// -----------------------------------------------------


					
			const existingTemplate =
				mappings.find(
					row =>
						Number(row.has_variants) === 1
				);

			// -----------------------------------------------------
			// Mapping Mode
			// Only show this when setting is enabled
			// -----------------------------------------------------

			if (allowIndividualItemMapping) {

				// Find whether existing mapping looks like
				// individual-item mapping.
				const hasExistingTemplate =
					!!existingTemplate;

				// fields.push({

				// 	fieldname:
				// 		'mapping_mode',

				// 	label:
				// 		__('Mapping Mode'),

				// 	fieldtype:
				// 		'Select',

				// 	options:
				// 		'variant_structure\nindividual_item',

				// 	default:'variant_structure',
				// 	reqd:
				// 		1,

				// 	description:
				// 		__(
				// 			'Choose whether Shopify variants should map to ERPNext Variants or individual ERPNext Items.'
				// 		)

				// });

				const hasExistingIndividualMapping =
					mappings.length > 0 &&
					mappings.every(
						row =>
							Number(row.has_variants) === 0 &&
							String(row.variant_id || '')
					);

				fields.push({
					fieldname: 'mapping_mode',
					label: __('Mapping Mode'),
					fieldtype: 'Select',
					options: 'variant_structure\nindividual_item',

					default:
						hasExistingIndividualMapping
							? 'individual_item'
							: 'variant_structure',

					reqd: 1,

					description: __(
						'Choose whether Shopify variants should map to ERPNext Variants or individual ERPNext Items.'
					)
				});

			}

			// -----------------------------------------------------
			// ERP Template
			// -----------------------------------------------------

			// fields.push({

			// 	fieldname:
			// 		'template_item',

			// 	label:
			// 		__('ERPNext Template'),

			// 	fieldtype:
			// 		'Link',

			// 	options:
			// 		'Item',

			// 	reqd:
			// 		0,

			// 	depends_on: 'eval:doc.mapping_mode=="variant_structure"',

			// 	default:
			// 		existingTemplate
			// 			?.erpnext_item_code || '',

			// 	get_query() {

			// 		return {

			// 			filters: {

			// 				disabled: 0,

			// 				is_sales_item: 1,

			// 				has_variants: 1

			// 			}

			// 		};

			// 	}

			// });

			// -----------------------------------------------------
			// ERP Template
			// -----------------------------------------------------

			const templateField = {

				fieldname:
					'template_item',

				label:
					__('ERPNext Template'),

				fieldtype:
					'Link',

				options:
					'Item',

				reqd:
					0,

				default:
					existingTemplate
						?.erpnext_item_code || '',

				get_query() {

					return {

						filters: {

							disabled: 0,

							is_sales_item: 1,

							has_variants: 1

						}

					};

				}

			};

			// ---------------------------------------------------------
			// Only make Template conditional when the new setting
			// is enabled.
			// ---------------------------------------------------------

			if (allowIndividualItemMapping) {

				templateField.depends_on =
					'eval:doc.mapping_mode=="variant_structure"';

			}

			fields.push(templateField);

			fields.push({

				fieldtype:
					'Section Break',

				label:
					__('Variant Mapping')

			});

			// -----------------------------------------------------
			// Shopify variants
			// -----------------------------------------------------

			variants.forEach(
				variant => {

					const existingVariant =
						mappings.find(
							row =>
								String(
									row.variant_id || ''
								) ===
								String(
									variant.id
								)
						);

					fields.push({

						fieldname:
							`variant_${variant.id}`,

						label:
							`${variant.title || 'Default Title'}` +
							(
								variant.sku
									? ` (${variant.sku})`
									: ''
							),

						fieldtype:
							'Link',

						options:
							'Item',

						reqd:
							1,

						default:
							existingVariant
								?.erpnext_item_code || '',

						get_query() {

							// -----------------------------------------
							// Get selected mapping mode
							// -----------------------------------------

							const mappingMode =
								dialog?.get_value(
									'mapping_mode'
								) ||
								'variant_structure';

							// -----------------------------------------
							// NEW MODE
							// Shopify Variant → Individual Item
							// -----------------------------------------

							if (
								mappingMode ===
								'individual_item'
							) {

								return {

									filters: {

										disabled: 0,

										is_sales_item: 1,

										has_variants: 0

									}

								};

							}

							// -----------------------------------------
							// EXISTING MODE
							// Shopify Variant → ERP Variant
							// -----------------------------------------

							const template =
								dialog?.get_value(
									'template_item'
								);

							if (!template) {

								return {

									filters: {

										disabled: 0,

										is_sales_item: 1,

										has_variants: 0

									}

								};

							}

							return {

								filters: {

									disabled: 0,

									is_sales_item: 1,

									has_variants: 0,

									variant_of:
										template

								}

							};

						}

					});

				}
			);

		}

		// =========================================================
		// DIALOG
		// =========================================================

		dialog = new frappe.ui.Dialog({

			title:
				__('Map Shopify Product'),

			fields:
				fields,

			size:
				hasVariants
					? 'large'
					: 'small',

			primary_action_label:
				__('Save Mapping'),

			primary_action:
				async values => {

					try {

						const mappingData = [];

						// =========================================
						// SIMPLE PRODUCT
						// =========================================

						if (!hasVariants) {

							const variant =
								variants[0];

							mappingData.push({

								erp_item_code:
									values.erp_item_code,

								variant_id:
									String(
										variant.id
									),

								sku:
									variant.sku || '',

								has_variants:
									0,

								variant_of:
									''

							});

						}

						// =========================================
						// VARIANT PRODUCT
						// =========================================

						else {

							// -----------------------------------------
							// Determine mapping mode
							// -----------------------------------------

							const mappingMode =
								values.mapping_mode ||
								'variant_structure';

							// =========================================
							// NEW MODE
							// Shopify Variant → Individual ERP Item
							// =========================================

							if (
								mappingMode ===
								'individual_item'
							) {

								variants.forEach(
									variant => {

										mappingData.push({

											erp_item_code:
												values[
													`variant_${variant.id}`
												],

											variant_id:
												String(
													variant.id
												),

											sku:
												variant.sku || '',

											has_variants:
												0,

											variant_of:
												''

										});

									}
								);

							}

							// =========================================
							// EXISTING MODE
							// Shopify Product → Template → Variants
							// =========================================

							else {

								// -------------------------------
								// Template mapping
								// -------------------------------

								mappingData.push({

									erp_item_code:
										values.template_item,

									variant_id:
										'',

									sku:
										'',

									has_variants:
										1,

									variant_of:
										''

								});

								// -------------------------------
								// Variant mappings
								// -------------------------------

								variants.forEach(
									variant => {

										mappingData.push({

											erp_item_code:
												values[
													`variant_${variant.id}`
												],

											variant_id:
												String(
													variant.id
												),

											sku:
												variant.sku || '',

											has_variants:
												0,

											variant_of:
												values.template_item

										});

									}
								);

							}

						}

						// =========================================
						// SAVE
						// =========================================

						const primaryButton =
							dialog.get_primary_btn();

						primaryButton
							.prop(
								'disabled',
								true
							)
							.text(
								__('Saving...')
							);

						const {
							message
						} = await frappe.call({

							method:
								'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.map_shopify_product',

							args: {

								product_id:
									productId,

								mappings:
									JSON.stringify(
										mappingData
									),

								mapping_mode:
									hasVariants
										? (
											values.mapping_mode ||
											'variant_structure'
										)
										: 'simple'

							}

						});

						if (!message?.success) {

							frappe.throw(
								__(
									'Unable to save Shopify mapping.'
								)
							);

						}

						// =========================================
						// SUCCESS
						// =========================================

						dialog.hide();

						frappe.show_alert({

							message:
								__(
									'Shopify product mapping saved successfully.'
								),

							indicator:
								'green'

						});

						// =========================================
						// Update local product
						// =========================================

						product.synced = true;

						// For simple product
						if (!hasVariants) {

							product.erp_item_code =
								values.erp_item_code;

						}

						// For variant product
						else {

							// Existing Template → Variant mode
							if (
								(
									values.mapping_mode ||
									'variant_structure'
								) ===
								'variant_structure'
							) {

								product.erp_item_code =
									values.template_item;

							}

							// Individual Item mode
							else {

								// There is no single ERPNext
								// template/item for this product.
								// Use first mapped item for display.
								const firstVariant =
									variants[0];

								product.erp_item_code =
									values[
										`variant_${firstVariant.id}`
									] || '';

							}

						}

						// =========================================
						// Refresh table
						// =========================================

						const search =
							this.productSearch || '';

						const filteredProducts =
							this.allShopifyProducts.filter(
								p => {

									const name =
										(
											p.title || ''
										).toLowerCase();

									return name.includes(
										search
									);

								}
							);

						this.shopifyProductTable.refresh(
							this.formatShopifyProducts(
								filteredProducts
							)
						);

						this.fetchProductCount();

					}

					catch (error) {

						console.error(
							error
						);

						dialog
							.get_primary_btn()
							.prop(
								'disabled',
								false
							)
							.text(
								__('Save Mapping')
							);

						frappe.throw(
							__(
								'Error saving Shopify mapping.'
							)
						);

					}

				}

		});

		dialog.show();
	}

	async fetchProductCount() {

		try {
			const { message: { erpnextCount, shopifyCount, syncedCount } } = await frappe.call({ method: 'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.get_product_count' });

			this.wrapper.find('#count-products-shopify').text(shopifyCount);
			this.wrapper.find('#count-products-erpnext').text(erpnextCount);
			this.wrapper.find('#count-products-synced').text(syncedCount);

		} catch (error) {
			frappe.throw(__('Error fetching product count.'));
		}

	}

	// async addTable() {

	// 	const listElement = this.wrapper.find('#shopify-product-list')[0];
	// 	this.shopifyProductTable = new frappe.DataTable(listElement, {
	// 		columns: [
	// 			// {
	// 			//     name: 'Image',
	// 			//     align: 'center',
	// 			// },
	// 			{
	// 				name: 'ID',
	// 				align: 'left',
	// 				editable: false,
	// 				focusable: false,
	// 			},
	// 			{
	// 				name: 'Name',
	// 				editable: false,
	// 				focusable: false,
	// 			},
	// 			{
	// 				name: 'SKUs',
	// 				editable: false,
	// 				focusable: false,
	// 			},
	// 			{
	// 				name: 'Status',
	// 				align: 'center',
	// 				editable: false,
	// 				focusable: false,
	// 			},
	// 			{
	// 				name: 'Action',
	// 				align: 'center',
	// 				editable: false,
	// 				focusable: false,
	// 			},
	// 		],
	// 		data: await this.fetchShopifyProducts(),
	// 		layout: 'fixed',
	// 	});

	// 	this.wrapper.find('.shopify-datatable-footer').show();

	// }
	async addTable() {

		const listElement = this.wrapper.find('#shopify-product-list')[0];

		// Fetch ALL Shopify products
		const products = await this.fetchAllShopifyProducts();

		this.shopifyProductTable = new frappe.DataTable(listElement, {

			columns: [
				{
					name: 'ID',
					align: 'left',
					editable: false,
					focusable: false,
				},
				{
					name: 'Name',
					editable: false,
					focusable: false,
				},
				{
					name: 'SKUs',
					editable: false,
					focusable: false,
				},
				{
					name: 'ERP Item',
					editable: false,
					focusable: false,
				},
				{
					name: 'Status',
					align: 'center',
					editable: false,
					focusable: false,
				},
				{
					name: 'Action',
					align: 'center',
					editable: false,
					focusable: false,
				},

			],

			data: this.formatShopifyProducts(products),

			layout: 'fixed',
		});

		this.wrapper.find('.shopify-datatable-footer').show();
	}


	// formatShopifyProducts(products) {

	// 	return products.map((product) => ({

	// 		'ID': product.id,

	// 		'Name': product.title,

	// 		'SKUs': product.variants
	// 			? product.variants.map(a => `${a.sku}`).join(', ')
	// 			: '',

	// 		'Status': this.getProductSyncStatus(product.synced),

	// 		'Action': !product.synced
	// 			? `<button type="button"
	// 					class="btn btn-default btn-xs btn-sync mx-2"
	// 					data-product="${product.id}">
	// 					Sync
	// 			</button>`
	// 			: `<button type="button"
	// 					class="btn btn-default btn-xs btn-resync mx-2"
	// 					data-product="${product.id}">
	// 					Re-sync
	// 			</button>`,

	// 	}));
	// }

	formatShopifyProducts(products) {

		return products.map((product) => {

			const erpItem = product.erp_item_code || '';

			const erpItemHtml = erpItem
				? `
					<button
						type="button"
						class="btn btn-default btn-xs btn-map-erp"
						data-product="${product.id}"
						title="${__('Edit ERPNext Item Mapping')}"
					>
						${frappe.utils.escape_html(erpItem)}
					</button>
				`
				: `
					<button
						type="button"
						class="btn btn-default btn-xs btn-map-erp"
						data-product="${product.id}"
					>
						${__('Map ERP Item')}
					</button>
				`;

			const actionHtml = !product.synced
				? `
					<button
						type="button"
						class="btn btn-default btn-xs btn-sync mx-2"
						data-product="${product.id}"
					>
						Sync
					</button>
				`
				: `
					<button
						type="button"
						class="btn btn-default btn-xs btn-resync mx-2"
						data-product="${product.id}"
					>
						Re-sync
					</button>
				`;

			return {

				'ID': product.id,

				'Name': product.title,

				'SKUs': product.variants
					? product.variants
						.map(a => `${a.sku || ''}`)
						.join(', ')
					: '',

				'ERP Item': erpItemHtml,

				'Status': this.getProductSyncStatus(
					product.synced
				),

				'Action': actionHtml,

			};

		});
	}

	async fetchAllShopifyProducts() {

		let allProducts = [];
		let from_ = null;

		try {

			while (true) {

				const { message } = await frappe.call({
					method: 'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.get_shopify_products',
					args: {
						from_: from_
					}
				});

				const products = message.products || [];

				allProducts = allProducts.concat(products);

				// No more pages
				if (!message.nextUrl) {
					break;
				}

				// Get next Shopify page
				from_ = message.nextUrl;
			}

			this.allShopifyProducts = allProducts;

			return allProducts;

		} catch (error) {

			console.error(error);

			frappe.throw(__('Error fetching Shopify products.'));
		}
	}

    // async fetchShopifyProducts(from_ = null, search = '') {
	// 	try {

	// 		const { message: { products, nextUrl, prevUrl } } = await frappe.call({
	// 			method: 'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.get_shopify_products',
	// 			args: {
	// 				from_: from_,
	// 				search: search
	// 			}
	// 		});
	// 		this.nextUrl = nextUrl;
	// 		this.prevUrl = prevUrl;

	// 		const shopifyProducts = products.map((product) => ({
	// 			// 'Image': product.image && product.image.src && `<img style="height: 50px" src="${product.image.src}">`,
	// 			'ID': product.id,
	// 			'Name': product.title,
	// 			'SKUs': product.variants && product.variants.map(a => `${a.sku}`).join(', '),
	// 			'Status': this.getProductSyncStatus(product.synced),
    //             'Action': !product.synced ?
    //                 `<button type="button" class="btn btn-default btn-xs btn-sync mx-2" data-product="${product.id}"> Sync </button>` :
    //                 `<button type="button" class="btn btn-default btn-xs btn-resync mx-2" data-product="${product.id}"> Re-sync </button>`,
	// 		}));

	// 		return shopifyProducts;
	// 	} catch (error) {
	// 		frappe.throw(__('Error fetching products.'));
	// 	}

	// }

	async fetchShopifyProducts(from_ = null) {

		try {

			const {
				message: {
					products,
					nextUrl,
					prevUrl
				}
			} = await frappe.call({

				method: 'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.get_shopify_products',

				args: {
					from_: from_
				}

			});

			this.nextUrl = nextUrl;
			this.prevUrl = prevUrl;

			return products;

		} catch (error) {

			console.error(error);

			frappe.throw(__('Error fetching products.'));
		}
	}

	getProductSyncStatus(status) {

		return status ?
			`<span class="indicator-pill green">Synced</span>` :
			`<span class="indicator-pill orange">Not Synced</span>`;

	}

	listen() {

		// Product name search
		let searchTimeout;

		this.wrapper.on('input', '#shopify-product-search', (e) => {

			const search = $(e.currentTarget)
				.val()
				.trim()
				.toLowerCase();

			clearTimeout(searchTimeout);

			searchTimeout = setTimeout(() => {

				this.productSearch = search;

				const filteredProducts = this.allShopifyProducts.filter(product => {

					const productName = (product.title || '').toLowerCase();

					return productName.includes(search);

				});

				const tableData = this.formatShopifyProducts(filteredProducts);

				this.shopifyProductTable.refresh(tableData);

			}, 300);
		});

		// sync a product from table
		this.wrapper.on('click', '.btn-sync', e => {

			const _this = $(e.currentTarget);

			_this.prop('disabled', true).text('Syncing...');

			const product = _this.attr('data-product');
			this.syncProduct(product)
				.then(status => {

					if (!status) {
						frappe.throw(__('Error syncing product'));
						_this.prop('disabled', false).text('Sync');
						return;
					}

					_this.parents('.dt-row')
						.find('.indicator-pill')
						.replaceWith(this.getProductSyncStatus(true));

                    _this.replaceWith(`<button type="button" class="btn btn-default btn-xs btn-resync mx-2" data-product="${product}"> Re-sync </button>`);

				});

		});

        this.wrapper.on('click', '.btn-resync', e => {
            const _this = $(e.currentTarget);

            _this.prop('disabled', true).text('Syncing...');

            const product = _this.attr('data-product');
            this.resyncProduct(product)
                .then(status => {

                    if (!status) {
                        frappe.throw(__('Error syncing product'));
                        return;
                    }

                    _this.parents('.dt-row')
                        .find('.indicator-pill')
                        .replaceWith(this.getProductSyncStatus(true));

                        _this.prop('disabled', false).text('Re-sync');

                })
                .catch(ex => {
                    _this.prop('disabled', false).text('Re-sync');
                    frappe.throw(__('Error syncing Product'));
                });
        });

		// pagination
		this.wrapper.on('click', '.btn-prev,.btn-next', e => this.switchPage(e));

		// sync all products
		this.wrapper.on('click', '#btn-sync-all', e => this.syncAll(e));


		// Map / Edit ERPNext Item
		this.wrapper.on(
			'click',
			'.btn-map-erp',
			e => {

				const productId = $(e.currentTarget)
					.attr('data-product');

				this.openMappingDialog(productId);
			}
		);

	}

	// async syncProduct(product) {

	// 	const { message: status } = await frappe.call({
	// 		method: 'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.sync_product',
	// 		args: { product },
	// 	});

	// 	if (status)
	// 		this.fetchProductCount();

	// 	return status;

	// }

	async syncProduct(product) {

		console.log("=================================");
		console.log("SHOPIFY SYNC START");
		console.log("Product ID:", product);
		console.log("=================================");

		try {

			console.log("Calling Python sync_product...");

			const response = await frappe.call({
				method:
					'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.sync_product',

				args: {
					product: product
				}
			});

			console.log("Python response received:");
			console.log(response);

			console.log("Python message:", response.message);

			if (response.message) {
				console.log("SYNC SUCCESS");
				this.fetchProductCount();
			} else {
				console.error("SYNC FAILED - Python returned false");
			}

			return response.message;

		} catch (error) {

			console.error("=================================");
			console.error("SHOPIFY SYNC JS ERROR");
			console.error(error);
			console.error("=================================");

			throw error;
		}
	}

    async resyncProduct(product) {

        const { message: status } = await frappe.call({
            method: 'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.resync_product',
            args: { product },
        });

        if (status)
            this.fetchProductCount();

        return status;

    }

	async switchPage({ currentTarget }) {

		const _this = $(currentTarget);

		$('.btn-paginate').prop('disabled', true);
		this.shopifyProductTable.showToastMessage('Loading...');

		const newProducts = await this.fetchShopifyProducts(
			_this.hasClass('btn-next') ? this.nextUrl : this.prevUrl
		);

		this.shopifyProductTable.refresh(newProducts);

		$('.btn-paginate').prop('disabled', false);
		this.shopifyProductTable.clearToastMessage();

	}

	syncAll() {

		this.checkSyncStatus();
		this.toggleSyncAllButton();

		if (this.syncRunning) {
			frappe.msgprint(__('Sync already in progress'));
		} else {
			frappe.call({ method: 'ecommerce_integrations.shopify.page.shopify_import_products.shopify_import_products.import_all_products' })
		}

		// sync progress
		this.logSync();

	}

	logSync() {

		const _log = $('#shopify-sync-log');
		_log.parents('.card').show();
		_log.text(''); // clear logs

		// define counters here to prevent calling jquery every time
		const _syncedCounter = $('#count-products-synced');
		const _erpnextCounter = $('#count-products-erpnext');

		frappe.realtime.on('shopify.key.sync.all.products', ({ message, synced, done, error }) => {

			message = `<pre class="mb-0">${message}</pre>`;
			_log.append(message);
			_log.scrollTop(_log[0].scrollHeight)

			if (synced) this.updateSyncedCount(_syncedCounter, _erpnextCounter);

			if (done) {
				frappe.realtime.off('shopify.key.sync.all.products');
				this.toggleSyncAllButton(false);
				this.fetchProductCount();
				this.syncRunning = false;
			}

		})

	}

	toggleSyncAllButton(disable = true) {

		const btn = $('#btn-sync-all');

		const _toggleClass = d => d ? 'btn-success' : 'btn-primary';
		const _toggleText = () => disable ? 'Syncing...' : 'Sync Products';

		btn.prop('disabled', disable)
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
}




