SETTINGS_DOCTYPE = "Unicommerce Settings"
MODULE_NAME = "unicommerce"


API_ENDPOINTS = {
	"get_item": "/services/rest/v1/catalog/itemType/get",
	"search_item": "/services/rest/v1/product/itemType/search",
	"get_sales_order": "/services/rest/v1/oms/saleorder/get",
	"search_sales_order": "/services/rest/v1/oms/saleOrder/search",
	"create_update_item": "/services/rest/v1/catalog/itemType/createOrEdit",
	"bulk_inventory_sync": "/services/rest/v1/inventory/adjust/bulk",
}

DEFAULT_WEIGHT_UOM = "Gram"


# Custom fields
ITEM_SYNC_CHECKBOX = "sync_with_unicommerce"
ORDER_CODE_FIELD = "unicommerce_order_code"
CHANNEL_ID_FIELD = "unicommerce_channel_id"
