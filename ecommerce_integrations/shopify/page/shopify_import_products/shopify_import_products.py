
from time import process_time

import frappe
from frappe import _
from frappe.exceptions import UniqueValidationError
from frappe.utils import cstr, now
from shopify.resources import Product

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import MODULE_NAME
from ecommerce_integrations.shopify.product import ShopifyProduct



# constants
SYNC_JOB_NAME = "shopify.job.sync.all.products"
REALTIME_KEY = "shopify.key.sync.all.products"


#Original
# @frappe.whitelist()
# def get_shopify_products(from_=None):
# 	shopify_products = fetch_all_products(from_)
# 	return shopify_products


@frappe.whitelist()
def get_shopify_products(from_=None, search=None):
	shopify_products = fetch_all_products(from_, search)
	return shopify_products



# def fetch_all_products(from_=None, search=None):
# 	# format shopify collection for datatable

# 	collection = _fetch_products_from_shopify(
# 		from_=from_,
# 		search=search
# 	)

# 	products = []
# 	for product in collection:
# 		d = product.to_dict()
# 		d["synced"] = is_synced(product.id)
# 		products.append(d)

# 	next_url = None
# 	if collection.has_next_page():
# 		next_url = collection.next_page_url

# 	prev_url = None
# 	if collection.has_previous_page():
# 		prev_url = collection.previous_page_url

# 	return {
# 		"products": products,
# 		"nextUrl": next_url,
# 		"prevUrl": prev_url,
# 	}


def fetch_all_products(from_=None, search=None):

    collection = _fetch_products_from_shopify(
        from_=from_,
        search=search
    )

    products = []

    for product in collection:

        d = product.to_dict()

        # Existing sync status
        d["synced"] = is_synced(product.id)

        # ERPNext Item mapped to this Shopify product
        d["erp_item_code"] = get_shopify_erp_item(product.id)

        products.append(d)

    next_url = None

    if collection.has_next_page():
        next_url = collection.next_page_url

    prev_url = None

    if collection.has_previous_page():
        prev_url = collection.previous_page_url

    return {
        "products": products,
        "nextUrl": next_url,
        "prevUrl": prev_url,
    }


# @temp_shopify_session
# def _fetch_products_from_shopify(from_=None, limit=20):
# 	if from_:
# 		collection = Product.find(from_=from_)
# 	else:
# 		collection = Product.find(limit=limit)

# 	return collection

@temp_shopify_session
def _fetch_products_from_shopify(from_=None, limit=20, search=None):

	if from_:
		collection = Product.find(from_=from_)

	else:
		if search:
			collection = Product.find(
				limit=limit,
				title=search
			)
		else:
			collection = Product.find(
				limit=limit
			)

	return collection
@frappe.whitelist()
def get_product_count():
	items = frappe.db.get_list("Item", {"variant_of": ["is", "not set"]})
	erpnext_count = len(items)

	sync_items = frappe.db.get_list("Ecommerce Item", {"variant_of": ["is", "not set"]})
	synced_count = len(sync_items)

	shopify_count = get_shopify_product_count()

	return {
		"shopifyCount": shopify_count,
		"syncedCount": synced_count,
		"erpnextCount": erpnext_count,
	}


@temp_shopify_session
def get_shopify_product_count():
	return Product.count()


@frappe.whitelist()
def sync_product(product):
	try:
		shopify_product = ShopifyProduct(product)
		shopify_product.sync_product()

		return True
	except Exception:
		frappe.db.rollback()
		return False


@frappe.whitelist()
def resync_product(product):
	return _resync_product(product)


@temp_shopify_session
def _resync_product(product):
	savepoint = "shopify_resync_product"
	try:
		item = Product.find(product)

		frappe.db.savepoint(savepoint)
		for variant in item.variants:
			shopify_product = ShopifyProduct(product, variant_id=variant.id)
			shopify_product.sync_product()

		return True
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		return False


def is_synced(product):
	return ecommerce_item.is_synced(MODULE_NAME, integration_item_code=product)


@frappe.whitelist()
def import_all_products():
	frappe.enqueue(
		queue_sync_all_products,
		queue="long",
		job_name=SYNC_JOB_NAME,
		key=REALTIME_KEY,
	)


def queue_sync_all_products(*args, **kwargs):
	start_time = process_time()

	counts = get_product_count()
	publish("Syncing all products...")

	if counts["shopifyCount"] < counts["syncedCount"]:
		publish("⚠ Shopify has less products than ERPNext.")

	_sync = True
	collection = _fetch_products_from_shopify(limit=100)
	savepoint = "shopify_product_sync"
	while _sync:
		for product in collection:
			try:
				publish(f"Syncing product {product.id}", br=False)
				frappe.db.savepoint(savepoint)
				if is_synced(product.id):
					publish(f"Product {product.id} already synced. Skipping...")
					continue

				shopify_product = ShopifyProduct(product.id)
				shopify_product.sync_product()

				publish(f"✅ Synced Product {product.id}", synced=True)

			except UniqueValidationError as e:
				publish(f"❌ Error Syncing Product {product.id} : {e!s}", error=True)
				frappe.db.rollback(save_point=savepoint)
				continue

			except Exception as e:
				publish(f"❌ Error Syncing Product {product.id} : {e!s}", error=True)
				frappe.db.rollback(save_point=savepoint)
				continue

		if collection.has_next_page():
			frappe.db.commit()  # prevents too many write request error
			collection = _fetch_products_from_shopify(from_=collection.next_page_url)
		else:
			_sync = False

	end_time = process_time()
	publish(f"🎉 Done in {end_time - start_time}s", done=True)
	return True


def publish(message, synced=False, error=False, done=False, br=True):
	frappe.publish_realtime(
		REALTIME_KEY,
		{
			"synced": synced,
			"error": error,
			"message": message + ("<br /><br />" if br else ""),
			"done": done,
		},
	)



def get_shopify_erp_item(product_id):

    product_id = cstr(product_id)

    # First look for the template mapping
    template_item = frappe.db.get_value(
        "Ecommerce Item",
        {
            "integration": MODULE_NAME,
            "integration_item_code": product_id,
            "has_variants": 1,
        },
        "erpnext_item_code",
    )

    if template_item:
        return template_item

    # Simple product
    return frappe.db.get_value(
        "Ecommerce Item",
        {
            "integration": MODULE_NAME,
            "integration_item_code": product_id,
        },
        "erpnext_item_code",
    )
    

@frappe.whitelist()
def get_shopify_mapping(product_id):

    product_id = cstr(product_id)

    return frappe.get_all(
        "Ecommerce Item",
        filters={
            "integration": MODULE_NAME,
            "integration_item_code": product_id,
        },
        fields=[
            "name",
            "erpnext_item_code",
            "integration_item_code",
            "variant_id",
            "sku",
            "has_variants",
            "variant_of",
            "item_synced_on",
        ],
        order_by="creation asc",
    )
    

@frappe.whitelist()
def get_shopify_mapping(product_id):

    product_id = cstr(product_id)

    return frappe.get_all(
        "Ecommerce Item",
        filters={
            "integration": MODULE_NAME,
            "integration_item_code": product_id,
        },
        fields=[
            "name",
            "erpnext_item_code",
            "integration_item_code",
            "variant_id",
            "sku",
            "has_variants",
            "variant_of",
            "item_synced_on",
        ],
        order_by="creation asc",
    )


@frappe.whitelist()
def get_variant_mapping_setting():

    return frappe.db.get_single_value(
        "Shopify Setting",
        "allow_variant_to_individual_item"
    ) or 0

# @frappe.whitelist()
# def map_shopify_product(product_id, mappings):
@frappe.whitelist()
def map_shopify_product(product_id, mappings, mapping_mode=None):
    """
    Manually map an existing ERPNext Item/Items to a Shopify product.

    IMPORTANT:
    This function NEVER creates an ERPNext Item.

    It only creates/updates Ecommerce Item records.
    """

    if isinstance(mappings, str):
        mappings = frappe.parse_json(mappings)

    product_id = cstr(product_id)

    if not mappings:
        frappe.throw(_("No ERPNext mapping was provided."))

    # ---------------------------------------------------------
    # Get Shopify product
    # ---------------------------------------------------------

    shopify_product = _get_shopify_product_for_mapping(product_id)

    variants = shopify_product.variants or []

    has_variants = _shopify_product_has_variants(
        shopify_product
    )

    # ---------------------------------------------------------
    # Validate mapping
    # ---------------------------------------------------------

    # if has_variants:

    #     _validate_variant_mappings(
    #         product_id=product_id,
    #         mappings=mappings,
    #         variants=variants,
    #     )

    # else:

    #     _validate_simple_mapping(
    #         product_id=product_id,
    #         mappings=mappings,
    #         variants=variants,
    #     )


    if not has_variants:

        # ---------------------------------------------------------
        # Existing simple product behavior
        # ---------------------------------------------------------

        _validate_simple_mapping(
            product_id=product_id,
            mappings=mappings,
            variants=variants,
        )

    elif mapping_mode == "individual_item":

        # ---------------------------------------------------------
        # Check Shopify Setting
        # ---------------------------------------------------------

        allow_individual_item = frappe.db.get_single_value(
            "Shopify Setting",
            "allow_variant_to_individual_item"
        )

        if not allow_individual_item:

            frappe.throw(
                _(
                    "Individual ERPNext Item mapping is disabled "
                    "in Shopify Settings."
                )
            )

        # ---------------------------------------------------------
        # New Individual Item validation
        # ---------------------------------------------------------

        _validate_individual_item_mappings(
            product_id=product_id,
            mappings=mappings,
            variants=variants,
        )

    else:

        # ---------------------------------------------------------
        # Existing Template → Variant behavior
        # ---------------------------------------------------------

        _validate_variant_mappings(
            product_id=product_id,
            mappings=mappings,
            variants=variants,
        )
        
    # ---------------------------------------------------------
    # Save mappings
    # ---------------------------------------------------------

    savepoint = "shopify_manual_mapping"

    try:

        frappe.db.savepoint(savepoint)

        existing_mappings = frappe.get_all(
            "Ecommerce Item",
            filters={
                "integration": MODULE_NAME,
                "integration_item_code": product_id,
            },
            fields=[
                "name",
                "variant_id",
                "has_variants",
            ],
        )

        existing_by_key = {}

        for row in existing_mappings:

            key = _mapping_key(
                row.has_variants,
                row.variant_id,
            )

            existing_by_key[key] = row.name

        submitted_keys = set()

        created = []
        updated = []

        # -----------------------------------------------------
        # Insert / update requested mappings
        # -----------------------------------------------------

        for mapping in mappings:

            erp_item_code = cstr(
                mapping.get("erp_item_code")
            )

            variant_id = cstr(
                mapping.get("variant_id") or ""
            )

            sku = cstr(
                mapping.get("sku") or ""
            )

            has_variants_value = int(
                mapping.get("has_variants") or 0
            )

            variant_of = cstr(
                mapping.get("variant_of") or ""
            )

            key = _mapping_key(
                has_variants_value,
                variant_id,
            )

            submitted_keys.add(key)

            values = {
                "integration": MODULE_NAME,
                "erpnext_item_code": erp_item_code,
                "integration_item_code": product_id,
                "variant_id": variant_id,
                "sku": sku,
                "has_variants": has_variants_value,
                "variant_of": variant_of,
                "item_synced_on": now(),
            }

            existing_name = existing_by_key.get(key)

            if existing_name:

                doc = frappe.get_doc(
                    "Ecommerce Item",
                    existing_name
                )

                doc.update(values)

                doc.save(
                    ignore_permissions=True
                )

                updated.append(existing_name)

            else:

                doc = frappe.get_doc({
                    "doctype": "Ecommerce Item",
                    **values,
                })

                doc.insert(
                    ignore_permissions=True
                )

                created.append(doc.name)

        # -----------------------------------------------------
        # Remove mappings that are no longer part of mapping
        # -----------------------------------------------------

        for row in existing_mappings:

            key = _mapping_key(
                row.has_variants,
                row.variant_id,
            )

            if key not in submitted_keys:

                frappe.delete_doc(
                    "Ecommerce Item",
                    row.name,
                    ignore_permissions=True,
                )

        frappe.db.commit()

        return {
            "success": True,
            "created": created,
            "updated": updated,
        }

    except Exception:

        frappe.db.rollback(
            save_point=savepoint
        )

        raise
    

@temp_shopify_session
def _get_shopify_product_for_mapping(product_id):

    return Product.find(product_id)


def _shopify_product_has_variants(product):

    variants = product.variants or []

    if len(variants) > 1:
        return True

    if len(variants) == 1:

        title = cstr(
            getattr(
                variants[0],
                "title",
                ""
            )
        ).strip()

        if title and title.lower() != "default title":
            return True

    return False


def _mapping_key(has_variants, variant_id):

    if int(has_variants or 0):
        return "template"

    return f"variant:{cstr(variant_id)}"


def _validate_simple_mapping(
    product_id,
    mappings,
    variants,
):

    if len(mappings) != 1:

        frappe.throw(
            _(
                "A simple Shopify product must have exactly "
                "one ERPNext Item mapping."
            )
        )

    if not variants:

        frappe.throw(
            _("Shopify product {0} has no variant.").format(
                product_id
            )
        )

    mapping = mappings[0]

    erp_item_code = cstr(
        mapping.get("erp_item_code")
    )

    if not erp_item_code:

        frappe.throw(
            _("ERPNext Item is required.")
        )

    item = frappe.db.get_value(
        "Item",
        erp_item_code,
        [
            "name",
            "disabled",
            "is_sales_item",
            "has_variants",
            "variant_of",
        ],
        as_dict=True,
    )

    if not item:

        frappe.throw(
            _("ERPNext Item {0} does not exist.").format(
                erp_item_code
            )
        )

    if item.disabled:

        frappe.throw(
            _("ERPNext Item {0} is disabled.").format(
                erp_item_code
            )
        )

    if not item.is_sales_item:

        frappe.throw(
            _("ERPNext Item {0} is not a Sales Item.").format(
                erp_item_code
            )
        )

    if item.has_variants:

        frappe.throw(
            _(
                "Item {0} is a template. "
                "Please select an Item Variant for a simple Shopify product."
            ).format(
                erp_item_code
            )
        )

    shopify_variant_id = cstr(
        getattr(
            variants[0],
            "id",
            ""
        )
    )

    mapping_variant_id = cstr(
        mapping.get("variant_id") or ""
    )

    if mapping_variant_id != shopify_variant_id:

        frappe.throw(
            _(
                "The Shopify Variant ID does not match "
                "the Shopify product variant."
            )
        )

    mapping["has_variants"] = 0
    mapping["variant_of"] = ""
    

def _validate_individual_item_mappings(
    product_id,
    mappings,
    variants,
):
    shopify_variant_ids = {
        cstr(v.id)
        for v in variants
    }

    variant_mappings = [
        m for m in mappings
        if not int(m.get("has_variants") or 0)
    ]

    # ---------------------------------------------------------
    # Every Shopify variant must have a mapping
    # ---------------------------------------------------------
    if len(variant_mappings) != len(variants):
        frappe.throw(
            _("Every Shopify Variant must be mapped to an ERPNext Item.")
        )

    # ---------------------------------------------------------
    # Get Shopify variant IDs from submitted mapping
    # ---------------------------------------------------------
    mapped_variant_ids = {
        cstr(m.get("variant_id") or "")
        for m in variant_mappings
    }

    # ---------------------------------------------------------
    # Make sure all Shopify variants are mapped
    # ---------------------------------------------------------
    if mapped_variant_ids != shopify_variant_ids:
        frappe.throw(
            _("Every Shopify Variant must be mapped exactly once.")
        )

    # ---------------------------------------------------------
    # Prevent duplicate Shopify variant mappings
    # ---------------------------------------------------------
    if len(mapped_variant_ids) != len(variant_mappings):
        frappe.throw(
            _("The same Shopify Variant cannot be mapped more than once.")
        )

    # ---------------------------------------------------------
    # Validate every ERPNext Item
    # ---------------------------------------------------------
    for mapping in variant_mappings:

        erp_item_code = cstr(
            mapping.get("erp_item_code") or ""
        )

        variant_id = cstr(
            mapping.get("variant_id") or ""
        )

        if not erp_item_code:
            frappe.throw(
                _("ERPNext Item is required for Shopify Variant {0}.")
                .format(variant_id)
            )

        item = frappe.db.get_value(
            "Item",
            erp_item_code,
            [
                "name",
                "disabled",
                "is_sales_item",
                "has_variants",
            ],
            as_dict=True,
        )

        if not item:
            frappe.throw(
                _("ERPNext Item {0} does not exist.")
                .format(erp_item_code)
            )

        if item.disabled:
            frappe.throw(
                _("ERPNext Item {0} is disabled.")
                .format(erp_item_code)
            )

        if not item.is_sales_item:
            frappe.throw(
                _("ERPNext Item {0} is not a Sales Item.")
                .format(erp_item_code)
            )

        # Individual item mode MUST NOT allow template Items
        if item.has_variants:
            frappe.throw(
                _(
                    "ERPNext Item {0} is a Template. "
                    "Please select an individual Item."
                ).format(erp_item_code)
            )

        # Normalize mapping
        mapping["has_variants"] = 0
        mapping["variant_of"] = ""
    


def _validate_variant_mappings(product_id, mappings, variants):
    """
    Validate Shopify product mappings for the standard
    ERPNext Template + ERPNext Variant structure.
    """

    # ---------------------------------------------------------
    # Find the template mapping
    # ---------------------------------------------------------
    template_mappings = [
        mapping
        for mapping in mappings
        if mapping.get("has_variants") == 1
    ]

    if len(template_mappings) != 1:
        frappe.throw(
            _(
                "Exactly one ERPNext Template mapping is required "
                "for a Shopify product with variants."
            )
        )

    template_mapping = template_mappings[0]
    template_code = template_mapping.get("erp_item_code")

    if not template_code:
        frappe.throw(
            _("ERPNext Template is required.")
        )

    # ---------------------------------------------------------
    # Validate ERPNext Template
    # ---------------------------------------------------------
    if not frappe.db.exists("Item", template_code):
        frappe.throw(
            _("ERPNext Template Item {0} does not exist.").format(
                template_code
            )
        )

    template_item = frappe.get_doc("Item", template_code)

    if template_item.disabled:
        frappe.throw(
            _("ERPNext Template Item {0} is disabled.").format(
                template_code
            )
        )

    if not template_item.is_sales_item:
        frappe.throw(
            _("ERPNext Template Item {0} is not a Sales Item.").format(
                template_code
            )
        )

    if not template_item.has_variants:
        frappe.throw(
            _(
                "ERPNext Item {0} must be a Template Item "
                "(Has Variants must be enabled)."
            ).format(template_code)
        )

    # ---------------------------------------------------------
    # Shopify Variant IDs
    # ---------------------------------------------------------
    shopify_variant_ids = {
        str(variant.get("id"))
        for variant in variants
    }

    mapped_variant_ids = []

    # ---------------------------------------------------------
    # Validate variant mappings
    # ---------------------------------------------------------
    for mapping in mappings:
        if mapping.get("has_variants") == 1:
            continue

        variant_id = str(mapping.get("variant_id") or "")
        item_code = mapping.get("erp_item_code")

        if not variant_id:
            frappe.throw(
                _("Shopify Variant ID is required for variant mapping.")
            )

        if variant_id in mapped_variant_ids:
            frappe.throw(
                _("Duplicate Shopify Variant ID found: {0}").format(
                    variant_id
                )
            )

        mapped_variant_ids.append(variant_id)

        # -----------------------------------------------------
        # Variant must belong to Shopify product
        # -----------------------------------------------------
        if variant_id not in shopify_variant_ids:
            frappe.throw(
                _(
                    "Shopify Variant {0} does not belong to "
                    "Shopify Product {1}."
                ).format(
                    variant_id,
                    product_id,
                )
            )

        if not item_code:
            frappe.throw(
                _(
                    "ERPNext Item is required for Shopify Variant {0}."
                ).format(variant_id)
            )

        # -----------------------------------------------------
        # ERPNext Item must exist
        # -----------------------------------------------------
        if not frappe.db.exists("Item", item_code):
            frappe.throw(
                _("ERPNext Item {0} does not exist.").format(
                    item_code
                )
            )

        item = frappe.get_doc("Item", item_code)

        # -----------------------------------------------------
        # Item validations
        # -----------------------------------------------------
        if item.disabled:
            frappe.throw(
                _("ERPNext Item {0} is disabled.").format(
                    item_code
                )
            )

        if not item.is_sales_item:
            frappe.throw(
                _("ERPNext Item {0} is not a Sales Item.").format(
                    item_code
                )
            )

        # -----------------------------------------------------
        # Variant must belong to selected Template
        # -----------------------------------------------------
        if item.variant_of != template_code:
            frappe.throw(
                _(
                    "ERPNext Item {0} is not a variant of "
                    "Template {1}."
                ).format(
                    item_code,
                    template_code,
                )
            )

        # -----------------------------------------------------
        # Normalize mapping
        # -----------------------------------------------------
        mapping["has_variants"] = 0
        mapping["variant_of"] = template_code

    # ---------------------------------------------------------
    # Validate number of mappings
    # ---------------------------------------------------------
    variant_mappings = [
        mapping
        for mapping in mappings
        if mapping.get("has_variants") == 0
    ]

    if len(variant_mappings) != len(variants):
        frappe.throw(
            _(
                "All Shopify variants must have an ERPNext Variant "
                "mapping. Expected {0}, got {1}."
            ).format(
                len(variants),
                len(variant_mappings),
            )
        )

    # ---------------------------------------------------------
    # Validate that every Shopify variant is mapped
    # ---------------------------------------------------------
    if set(mapped_variant_ids) != shopify_variant_ids:
        frappe.throw(
            _(
                "The Shopify Variant mappings do not match "
                "the Shopify product variants."
            )
        )

    return True


