import frappe
from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE, OLD_SETTINGS_DOCTYPE


def boot_session(bootinfo):
	"""Don't show old doctypes after enabling new ones."""
	if frappe.get_cached_value(SETTING_DOCTYPE, SETTING_DOCTYPE, "enable_shopify"):
		try:
			bootinfo.single_types.remove(OLD_SETTINGS_DOCTYPE)
		except ValueError:
			pass
