# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log \
	import create_log

from ecommerce_integrations.shopify.constants import MODULE_NAME


def create_shopify_log(**kwargs):
	log = create_log(module_def=MODULE_NAME, **kwargs)
	return log

