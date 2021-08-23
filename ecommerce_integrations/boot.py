from ecommerce_integrations.shopify.constants import OLD_SETTINGS_DOCTYPE


def boot_session(bootinfo):
	"""Don't show old doctypes after enabling new ones."""
	try:
		bootinfo.single_types.remove(OLD_SETTINGS_DOCTYPE)
	except ValueError:
		pass
