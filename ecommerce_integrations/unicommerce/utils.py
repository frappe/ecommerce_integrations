from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log import (
	create_log,
)
from ecommerce_integrations.unicommerce.constants import MODULE_NAME


def create_unicommerce_log(**kwargs):
	return create_log(module_def=MODULE_NAME, **kwargs)
