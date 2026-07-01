# Copyright (c) 2022, Frappe and Contributors
# See license.txt

from frappe.exceptions import ValidationError

from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_repository import (
	validate_amazon_sp_api_credentials,
)
from ecommerce_integrations.amazon.tests.utils import AmazonTestSuite


class TestAmazonSPAPISettings(AmazonTestSuite):
	def test_validate_credentials(self):
		"""requirement: When improper credentials are provided, system throws error."""
		credentials = {
			"iam_arn": "********************",
			"client_id": "********************",
			"client_secret": "********************",
			"refresh_token": "********************",
			"aws_access_key": "********************",
			"aws_secret_key": "********************",
			"country": "US",
		}

		self.assertRaises(ValidationError, validate_amazon_sp_api_credentials, **credentials)
