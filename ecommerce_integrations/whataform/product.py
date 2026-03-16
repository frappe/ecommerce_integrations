import frappe

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item
from ecommerce_integrations.whataform.constants import MODULE_NAME
from ecommerce_integrations.whataform.utils import NoItemMapping, NoSkuInWebhookData


def get_item_code(whataform_item):
	"""Get item code using whataform_item dict.

	Item should contain product_id and maybe sku."""

	if sku := whataform_item.get("sku"):
		item = ecommerce_item.get_erpnext_item(
			integration=MODULE_NAME, integration_item_code=None, sku=sku,
		)
		if item:
			return item.item_code
		else:
			err = NoItemMapping(sku=sku)
			raise err
	else:
		err = NoSkuInWebhookData(data=whataform_item)
		raise err
