from frappe.model.document import Document


class SettingController(Document):
	def is_enabled(self) -> bool:
		"""Check if integration is enabled or not."""
		raise NotImplementedError()
