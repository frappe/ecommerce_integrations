# Copyright (c) 2022, Frappe and contributors
# For license information, please see license.txt


import datetime
import hashlib
import hmac

import boto3
from requests import request
from requests.auth import AuthBase
from requests.compat import urlparse

__all__ = [
	"SPAPIError",
	"Finances",
	"Orders",
	"CatalogItems",
]


# https://github.com/amzn/selling-partner-api-docs/blob/main/guides/en-US/developer-guide/SellingPartnerApiDeveloperGuide.md#selling-partner-api-endpoints
MARKETPLACES = {
	"North America": {
		"CA": "A2EUQ1WTGCTBG2",
		"US": "ATVPDKIKX0DER",
		"MX": "A1AM78C64UM0Y8",
		"BR": "A2Q3Y263D00KWC",
		"AWS Region": "us-east-1",
		"Endpoint": "https://sellingpartnerapi-na.amazon.com",
	},
	"Europe": {
		"ES": "A1RKKUPIHCS9HS",
		"GB": "A1F83G8C2ARO7P",
		"FR": "A13V1IB3VIYZZH",
		"NL": "A1805IZSGTT6HS",
		"DE": "A1PA6795UKMFR9",
		"IT": "APJ6JRA9NG5V4",
		"SE": "A2NODRKZP88ZB9",
		"PL": "A1C3SOZRARQ6R3",
		"EG": "ARBP9OOSHTCHU",
		"TR": "A33AVAJ2PDY3EV",
		"SA": "A17E79C6D8DWNP",
		"AE": "A2VIGQ35RCS4UG",
		"IN": "A21TJRUUN4KGV",
		"AWS Region": "eu-west-1",
		"Endpoint": "https://sellingpartnerapi-eu.amazon.com",
	},
	"Far East": {
		"SG": "A19VAU5U5O7RUS",
		"AU": "A39IBJ37TRP1C6",
		"JP": "A1VC38T7YXB528",
		"AWS Region": "us-west-2",
		"Endpoint": "https://sellingpartnerapi-fe.amazon.com",
	},
}

# Following code is adapted from https://github.com/andrewjroth/requests-auth-aws-sigv4 under the Apache License 2.0 with minor changes.

# Copyright 2020 Andrew J Roth <andrew@andrewjroth.com>

# Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

# 1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

# 2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

# 3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.


class AWSSigV4(AuthBase):
	def __init__(self, service, **kwargs):
		"""Create authentication mechanism

		:param service: AWS Service identifier, for example `ec2`.  This is required.
		:param region:  AWS Region, for example `us-east-1`.  If not provided, it will be set using
		    the environment variables `AWS_DEFAULT_REGION` or using boto3, if available.
		:param session: If boto3 is available, will attempt to get credentials using boto3,
		    unless passed explicitly.  If using boto3, the provided session will be used or a new
		    session will be created.

		"""

		self.service = service
		self.region = kwargs.get("region")
		self.aws_access_key_id = kwargs.get("aws_access_key_id")
		self.aws_session_token = kwargs.get("aws_session_token")
		self.aws_secret_access_key = kwargs.get("aws_secret_access_key")

		if not self.aws_access_key_id or not self.aws_secret_access_key:
			raise KeyError("AWS Access Key ID and Secret Access Key are required.")

		if self.region is None:
			raise KeyError("Region is required.")

	def __call__(self, request):
		"""Called to add authentication information to request

		:param request: `requests.models.PreparedRequest` object to modify

		:returns: `requests.models.PreparedRequest`, modified to add authentication

		"""

		# Create a date for headers and the credential string.
		time = datetime.datetime.utcnow()
		self.amzdate = time.strftime("%Y%m%dT%H%M%SZ")
		self.datestamp = time.strftime("%Y%m%d")

		# Parse request to get URL parts.
		parsed_url = urlparse(request.url)
		host = parsed_url.hostname
		uri = parsed_url.path

		if len(parsed_url.query) > 0:
			query_string = dict(map(lambda i: i.split("="), parsed_url.query.split("&")))
		else:
			query_string = dict()

		# Setup Headers.
		if "Host" not in request.headers:
			request.headers["Host"] = host
		if "Content-Type" not in request.headers:
			request.headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
		if "User-Agent" not in request.headers:
			request.headers["User-Agent"] = "python-amazon-mws/0.0.1 (Language=Python)"
		if self.aws_session_token:
			request.headers["x-amz-security-token"] = self.aws_session_token
		request.headers["X-AMZ-Date"] = self.amzdate

		# ************* TASK 1: CREATE A CANONICAL REQUEST *************
		# http://docs.aws.amazon.com/general/latest/gr/sigv4-create-canonical-request.html

		# Query string values must be URL-encoded (space=%20) and be sorted by name.
		canonical_query_string = "&".join(map(lambda p: "=".join(p), sorted(query_string.items())))

		# Create payload hash (hash of the request body content).
		if request.method == "GET":
			payload_hash = hashlib.sha256(("").encode("utf-8")).hexdigest()
		else:
			if request.body:
				if isinstance(request.body, bytes):
					payload_hash = hashlib.sha256(request.body).hexdigest()
				else:
					payload_hash = hashlib.sha256(request.body.encode("utf-8")).hexdigest()
			else:
				payload_hash = hashlib.sha256(b"").hexdigest()
		request.headers["x-amz-content-sha256"] = payload_hash

		# Create the canonical headers and signed headers. Header names
		# must be trimmed and lowercase, and sorted in code point order from
		# low to high. Note that there is a trailing \n.
		headers_to_sign = sorted(
			filter(
				lambda h: h.startswith("x-amz-") or h == "host",
				map(lambda H: H.lower(), request.headers.keys()),
			)
		)
		canonical_headers = "".join(
			map(lambda h: ":".join((h, request.headers[h])) + "\n", headers_to_sign)
		)
		signed_headers = ";".join(headers_to_sign)

		# Combine elements to create canonical request.
		canonical_request = "\n".join(
			[request.method, uri, canonical_query_string, canonical_headers, signed_headers, payload_hash]
		)

		# ************* TASK 2: CREATE THE STRING TO SIGN*************
		credential_scope = "/".join([self.datestamp, self.region, self.service, "aws4_request"])
		string_to_sign = "\n".join(
			[
				"AWS4-HMAC-SHA256",
				self.amzdate,
				credential_scope,
				hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
			]
		)

		# ************* TASK 3: CALCULATE THE SIGNATURE *************
		def sign(key, msg):
			return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

		key_date = sign(("AWS4" + self.aws_secret_access_key).encode("utf-8"), self.datestamp)
		key_region = sign(key_date, self.region)
		k_service = sign(key_region, self.service)
		key_signing = sign(k_service, "aws4_request")
		signature = hmac.new(key_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

		# ************* TASK 4: ADD SIGNING INFORMATION TO THE REQUEST *************
		request.headers["Authorization"] = (
			f"AWS4-HMAC-SHA256 Credential={self.aws_access_key_id}/{credential_scope},"
			f" SignedHeaders={signed_headers}, Signature={signature}"
		)

		return request


class SPAPIError(Exception):
	"""
	Main SP-API Exception class
	"""

	def __init__(self, *args, **kwargs) -> None:
		self.error = kwargs.get("error", "-")
		self.error_description = kwargs.get("error_description", "-")
		super().__init__(*args)


class SPAPI(object):
	""" Base Amazon SP-API class """

	# https://github.com/amzn/selling-partner-api-docs/blob/main/guides/en-US/developer-guide/SellingPartnerApiDeveloperGuide.md#connecting-to-the-selling-partner-api
	AUTH_URL = "https://api.amazon.com/auth/o2/token"

	BASE_URI = "/"

	def __init__(
		self,
		iam_arn: str,
		client_id: str,
		client_secret: str,
		refresh_token: str,
		aws_access_key: str,
		aws_secret_key: str,
		country_code: str = "US",
	) -> None:
		self.iam_arn = iam_arn
		self.client_id = client_id
		self.client_secret = client_secret
		self.refresh_token = refresh_token
		self.aws_access_key = aws_access_key
		self.aws_secret_key = aws_secret_key
		self.country_code = country_code
		self.region, self.endpoint, self.marketplace_id = Util.get_marketplace_data(country_code)

	def get_access_token(self) -> str:
		data = {
			"grant_type": "refresh_token",
			"client_id": self.client_id,
			"client_secret": self.client_secret,
			"refresh_token": self.refresh_token,
		}

		response = request(method="POST", url=self.AUTH_URL, data=data)
		result = response.json()
		if response.status_code == 200:
			return result.get("access_token")
		exception = SPAPIError(
			error=result.get("error"), error_description=result.get("error_description")
		)
		raise exception

	def get_auth(self) -> AWSSigV4:
		try:
			client = boto3.client(
				"sts",
				aws_access_key_id=self.aws_access_key,
				aws_secret_access_key=self.aws_secret_key,
				region_name=self.region,
			)

			response = client.assume_role(RoleArn=self.iam_arn, RoleSessionName="SellingPartnerAPI")

			credentials = response["Credentials"]
			access_key_id = credentials["AccessKeyId"]
			secret_access_key = credentials["SecretAccessKey"]
			session_token = credentials["SessionToken"]

			return AWSSigV4(
				service="execute-api",
				aws_access_key_id=access_key_id,
				aws_secret_access_key=secret_access_key,
				aws_session_token=session_token,
				region=self.region,
			)
		except Exception as e:
			raise SPAPIError(error="invalid_aws_credentials", error_description=e)

	def get_headers(self) -> dict:
		return {"x-amz-access-token": self.get_access_token()}

	def make_request(
		self, method: str = "GET", append_to_base_uri: str = "", params: dict = None, data: dict = None,
	) -> dict:
		if isinstance(params, dict):
			params = Util.remove_empty(params)
		if isinstance(data, dict):
			data = Util.remove_empty(data)

		url = self.endpoint + self.BASE_URI + append_to_base_uri

		response = request(
			method=method,
			url=url,
			params=params,
			data=data,
			headers=self.get_headers(),
			auth=self.get_auth(),
		)
		return response.json()

	def list_to_dict(self, key: str, values: list, data: dict) -> None:
		if values and isinstance(values, list):
			for idx in range(len(values)):
				data[f"{key}[{idx}]"] = values[idx]


class Finances(SPAPI):
	""" Amazon Finances API """

	BASE_URI = "/finances/v0/"

	def list_financial_events_by_order_id(
		self, order_id: str, max_results: int = None, next_token: str = None
	) -> dict:
		""" Returns all financial events for the specified order. """
		append_to_base_uri = f"orders/{order_id}/financialEvents"
		data = dict(MaxResultsPerPage=max_results, NextToken=next_token)
		return self.make_request(append_to_base_uri=append_to_base_uri, params=data)


class Orders(SPAPI):
	""" Amazon Orders API """

	BASE_URI = "/orders/v0/orders"

	def get_orders(
		self,
		created_after: str,
		created_before: str = None,
		last_updated_after: str = None,
		last_updated_before: str = None,
		order_statuses: list = None,
		marketplace_ids: list = None,
		fulfillment_channels: list = None,
		payment_methods: list = None,
		buyer_email: str = None,
		seller_order_id: str = None,
		max_results: int = 100,
		easyship_shipment_statuses: list = None,
		next_token: str = None,
		amazon_order_ids: list = None,
		actual_fulfillment_supply_source_id: str = None,
		is_ispu: bool = False,
		store_chain_store_id: str = None,
	) -> dict:
		""" Returns orders created or updated during the time frame indicated by the specified parameters. You can also apply a range of filtering criteria to narrow the list of orders returned. If NextToken is present, that will be used to retrieve the orders instead of other criteria. """
		data = dict(
			CreatedAfter=created_after,
			CreatedBefore=created_before,
			LastUpdatedAfter=last_updated_after,
			LastUpdatedBefore=last_updated_before,
			BuyerEmail=buyer_email,
			SellerOrderId=seller_order_id,
			MaxResultsPerPage=max_results,
			NextToken=next_token,
			ActualFulfillmentSupplySourceId=actual_fulfillment_supply_source_id,
			IsISPU=is_ispu,
			StoreChainStoreId=store_chain_store_id,
		)

		self.list_to_dict("OrderStatuses", order_statuses, data)
		self.list_to_dict("MarketplaceIds", marketplace_ids, data)
		self.list_to_dict("FulfillmentChannels", fulfillment_channels, data)
		self.list_to_dict("PaymentMethods", payment_methods, data)
		self.list_to_dict("EasyShipShipmentStatuses", easyship_shipment_statuses, data)
		self.list_to_dict("AmazonOrderIds", amazon_order_ids, data)

		if not marketplace_ids:
			marketplace_ids = [self.marketplace_id]
			data["MarketplaceIds"] = marketplace_ids

		return self.make_request(params=data)

	def get_order_items(self, order_id: str, next_token: str = None) -> dict:
		""" Returns detailed order item information for the order indicated by the specified order ID. If NextToken is provided, it's used to retrieve the next page of order items. """
		append_to_base_uri = f"/{order_id}/orderItems"
		data = dict(NextToken=next_token)
		return self.make_request(append_to_base_uri=append_to_base_uri, params=data)


class CatalogItems(SPAPI):
	""" Amazon Catalog Items API """

	BASE_URI = "/catalog/v0"

	def get_catalog_item(self, asin: str, marketplace_id: str = None,) -> dict:
		""" Returns a specified item and its attributes. """
		if not marketplace_id:
			marketplace_id = self.marketplace_id

		append_to_base_uri = f"/items/{asin}"
		data = dict(MarketplaceId=marketplace_id)

		return self.make_request(append_to_base_uri=append_to_base_uri, params=data)


class Util:
	@staticmethod
	def get_marketplace(country_code):
		for selling_region in MARKETPLACES:
			for country in MARKETPLACES.get(selling_region):
				if country_code == country:
					return MARKETPLACES.get(selling_region)
		else:
			raise KeyError(f"Invalid Country Code: {country_code}")

	@staticmethod
	def get_marketplace_data(country_code):
		marketplace = Util.get_marketplace(country_code)
		region = marketplace.get("AWS Region")
		endpoint = marketplace.get("Endpoint")
		marketplace_id = marketplace.get(country_code)

		return region, endpoint, marketplace_id

	@staticmethod
	def remove_empty(dict):
		"""
		Helper function that removes all keys from a dictionary (dict), that have an empty value.
		"""
		for key in list(dict):
			if not dict[key]:
				del dict[key]
		return dict
