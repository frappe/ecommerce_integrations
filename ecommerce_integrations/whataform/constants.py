# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE


MODULE_NAME = "whataform"
SETTING_DOCTYPE = "Whataform Setting"

EVENT_MAPPER = {
	"message": "ecommerce_integrations.whataform.order.process_message",
}

# custom fields

CUSTOMER_ID_FIELD = "whataform_customer_id"
ORDER_ID_FIELD = "whataform_order_id"
ORDER_NUMBER_FIELD = "whataform_order_number"
ORDER_ITEM_DISCOUNT_FIELD = "whataform_item_discount"
