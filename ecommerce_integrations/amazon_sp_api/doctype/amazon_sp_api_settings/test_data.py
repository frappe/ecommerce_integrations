DATA = {
	"get_orders_200": {
		"status": 200,
		"json": {
			"payload": {
				"CreatedBefore": "1.569521782042E9",
				"Orders": [
					{
						"AmazonOrderId": "902-1845936-5435065",
						"PurchaseDate": "1970-01-19T03:58:30Z",
						"LastUpdateDate": "1970-01-19T03:58:32Z",
						"OrderStatus": "Unshipped",
						"FulfillmentChannel": "MFN",
						"SalesChannel": "Amazon.com",
						"ShipServiceLevel": "Std US D2D Dom",
						"OrderTotal": {"CurrencyCode": "USD", "Amount": "11.01"},
						"NumberOfItemsShipped": 0,
						"NumberOfItemsUnshipped": 1,
						"PaymentMethod": "Other",
						"PaymentMethodDetails": ["Standard"],
						"IsReplacementOrder": False,
						"MarketplaceId": "ATVPDKIKX0DER",
						"ShipmentServiceLevelCategory": "Standard",
						"OrderType": "StandardOrder",
						"EarliestShipDate": "1970-01-19T03:59:27Z",
						"LatestShipDate": "1970-01-19T04:05:13Z",
						"EarliestDeliveryDate": "1970-01-19T04:06:39Z",
						"LatestDeliveryDate": "1970-01-19T04:15:17Z",
						"IsBusinessOrder": False,
						"IsPrime": False,
						"IsGlobalExpressEnabled": False,
						"IsPremiumOrder": False,
						"IsSoldByAB": False,
						"DefaultShipFromLocationAddress": {
							"Name": "MFNIntegrationTestMerchant",
							"AddressLine1": "2201 WESTLAKE AVE",
							"City": "SEATTLE",
							"StateOrRegion": "WA",
							"PostalCode": "98121-2778",
							"CountryCode": "US",
							"Phone": "+1 480-386-0930 ext. 73824",
							"AddressType": "Commercial",
						},
						"FulfillmentInstruction": {"FulfillmentSupplySourceId": "sampleSupplySourceId"},
						"IsISPU": False,
						"AutomatedShippingSettings": {"HasAutomatedShippingSettings": False},
					},
					{
						"AmazonOrderId": "902-8745147-1934268",
						"PurchaseDate": "1970-01-19T03:58:30Z",
						"LastUpdateDate": "1970-01-19T03:58:32Z",
						"OrderStatus": "Unshipped",
						"FulfillmentChannel": "MFN",
						"SalesChannel": "Amazon.com",
						"ShipServiceLevel": "Std US D2D Dom",
						"OrderTotal": {"CurrencyCode": "USD", "Amount": "11.01"},
						"NumberOfItemsShipped": 0,
						"NumberOfItemsUnshipped": 1,
						"PaymentMethod": "Other",
						"PaymentMethodDetails": ["Standard"],
						"IsReplacementOrder": False,
						"MarketplaceId": "ATVPDKIKX0DER",
						"ShipmentServiceLevelCategory": "Standard",
						"OrderType": "StandardOrder",
						"EarliestShipDate": "1970-01-19T03:59:27Z",
						"LatestShipDate": "1970-01-19T04:05:13Z",
						"EarliestDeliveryDate": "1970-01-19T04:06:39Z",
						"LatestDeliveryDate": "1970-01-19T04:15:17Z",
						"IsBusinessOrder": False,
						"IsPrime": False,
						"IsGlobalExpressEnabled": False,
						"IsPremiumOrder": False,
						"IsSoldByAB": False,
					},
				],
			}
		},
	},
	"get_order_items_200": {
		"status": 200,
		"json": {
			"payload": {
				"AmazonOrderId": "902-1845936-5435065",
				"OrderItems": [
					{
						"ASIN": "B008X9Z37A",
						"OrderItemId": "05015851154158",
						"SellerSKU": "NABetaASINB00551Q3CS",
						"Title": "B00551Q3CS [Card Book]",
						"QuantityOrdered": 1,
						"QuantityShipped": 0,
						"ProductInfo": {"NumberOfItems": 1},
						"ItemPrice": {"CurrencyCode": "USD", "Amount": "10.00"},
						"ItemTax": {"CurrencyCode": "USD", "Amount": "1.01"},
						"PromotionDiscount": {"CurrencyCode": "USD", "Amount": "0.00"},
						"IsGift": False,
						"ConditionId": "New",
						"ConditionSubtypeId": "New",
						"IsTransparency": False,
						"SerialNumberRequired": False,
						"IossNumber": "",
						"DeemedResellerCategory": "IOSS",
						"StoreChainStoreId": "ISPU_StoreId",
					}
				],
			}
		},
	},
	"get_catalog_item_200": {
		"status": 200,
		"json": {
			"asin": "B008X9Z37A",
			"identifiers": [
				{
					"marketplaceId": "ATVPDKIKX0DER",
					"identifiers": [
						{"identifierType": "ean", "identifier": "0887276302195"},
						{"identifierType": "upc", "identifier": "887276302195"},
					],
				}
			],
			"images": [
				{
					"marketplaceId": "ATVPDKIKX0DER",
					"images": [
						{
							"variant": "MAIN",
							"link": "https://m.media-amazon.com/images/I/51DZzp3w3vL.jpg",
							"height": 333,
							"width": 500,
						}
					],
				}
			],
			"productTypes": [{"marketplaceId": "ATVPDKIKX0DER", "productType": "TELEVISION"}],
			"salesRanks": [
				{
					"marketplaceId": "ATVPDKIKX0DER",
					"ranks": [
						{
							"title": "OLED TVs",
							"link": "http://www.amazon.com/gp/bestsellers/electronics/6463520011",
							"rank": 3,
						},
						{
							"title": "Electronics",
							"link": "http://www.amazon.com/gp/bestsellers/electronics",
							"rank": 1544,
						},
					],
				}
			],
			"summaries": [
				{
					"marketplaceId": "ATVPDKIKX0DER",
					"brandName": "Samsung Electronics",
					"browseNode": "6463520011",
					"colorName": "Black",
					"itemName": (
						"Samsung QN82Q60RAFXZA Flat 82-Inch QLED 4K Q60 Series (2019) Ultra HD"
						" Smart TV with HDR and Alexa Compatibility"
					),
					"manufacturer": "Samsung",
					"modelNumber": "QN82Q60RAFXZA",
					"sizeName": "82-Inch",
					"styleName": "TV only",
				}
			],
			"variations": [
				{"marketplaceId": "ATVPDKIKX0DER", "asins": ["B08J7TQ9FL"], "variationType": "CHILD"}
			],
			"vendorDetails": [
				{
					"marketplaceId": "ATVPDKIKX0DER",
					"brandCode": "SAMF9",
					"categoryCode": "50400100",
					"manufacturerCode": "SAMF9",
					"manufacturerCodeParent": "SAMF9",
					"productGroup": "Home Entertainment",
					"replenishmentCategory": "NEW_PRODUCT",
					"subcategoryCode": "50400150",
				}
			],
		},
	},
	"list_financial_events_by_order_id_200": {
		"status": 200,
		"json": {
			"payload": {
				"FinancialEvents": {
					"RetrochargeEventList": [
						{
							"RetrochargeEventType": "Retrocharge",
							"AmazonOrderId": "444-555-3343433",
							"PostedDate": "2020-02-05T13:56:00.363Z",
							"BaseTax": {"CurrencyCode": "USD", "CurrencyAmount": 25.37},
							"ShippingTax": {"CurrencyCode": "USD", "CurrencyAmount": 25.37},
							"MarketplaceName": "1",
							"RetrochargeTaxWithheldList": [
								{
									"TaxCollectionModel": "Free",
									"TaxesWithheld": [
										{"ChargeType": "Tax", "ChargeAmount": {"CurrencyCode": "USD", "CurrencyAmount": 25.37}}
									],
								}
							],
						}
					],
					"RentalTransactionEventList": [
						{
							"AmazonOrderId": "444-555-3343433",
							"RentalEventType": "string",
							"ExtensionLength": 0,
							"PostedDate": "2020-02-05T13:56:00.363Z",
							"RentalChargeList": [
								{"ChargeType": "Tax", "ChargeAmount": {"CurrencyCode": "USD", "CurrencyAmount": 25.37}}
							],
							"RentalFeeList": [
								{
									"FeeType": "FixedClosingFee",
									"FeeAmount": {"CurrencyCode": "USD", "CurrencyAmount": 25.37},
								}
							],
							"MarketplaceName": "1",
							"RentalInitialValue": {"CurrencyCode": "USD", "CurrencyAmount": 25.37},
							"RentalReimbursement": {"CurrencyCode": "USD", "CurrencyAmount": 25.37},
							"RentalTaxWithheldList": [
								{
									"TaxCollectionModel": "Free",
									"TaxesWithheld": [
										{"ChargeType": "Tax", "ChargeAmount": {"CurrencyCode": "USD", "CurrencyAmount": 25.37}}
									],
								}
							],
						}
					],
					"ProductAdsPaymentEventList": [
						{
							"postedDate": "2020-02-05T13:56:00.363Z",
							"transactionType": "Free",
							"invoiceId": "3454535453",
							"baseValue": {"CurrencyCode": "USD", "CurrencyAmount": 25.37},
							"taxValue": {"CurrencyCode": "USD", "CurrencyAmount": 25.37},
							"transactionValue": {"CurrencyCode": "USD", "CurrencyAmount": 25.37},
						}
					],
					"ServiceFeeEventList": [
						{
							"AmazonOrderId": "444-555-3343433",
							"FeeReason": "Free",
							"FeeList": [
								{
									"FeeType": "FixedClosingFee",
									"FeeAmount": {"CurrencyCode": "USD", "CurrencyAmount": 25.37},
								}
							],
							"SellerSKU": "456454455464",
							"FnSKU": "Fn134",
							"FeeDescription": "FeeDescription",
							"ASIN": "KJHJ457648GHD",
						}
					],
				},
			}
		},
	},
	"create_report_200": {"status": 200, "json": {"reportId": "ID323"}},
	"get_report_200": {
		"status": 200,
		"json": {
			"reportId": "ReportId1",
			"reportType": "FEE_DISCOUNTS_REPORT",
			"dataStartTime": "2019-12-11T13:47:20.677Z",
			"dataEndTime": "2019-12-12T13:47:20.677Z",
			"createdTime": "2019-12-10T13:47:20.677Z",
			"processingStatus": "DONE",
			"processingStartTime": "2019-12-10T13:47:20.677Z",
			"processingEndTime": "2019-12-12T13:47:20.677Z",
			"reportDocumentId": "0356cf79-b8b0-4226-b4b9-0ee058ea5760",
		},
	},
	"get_report_document_200": {
		"status": 200,
		"json": {
			"reportDocumentId": "0356cf79-b8b0-4226-b4b9-0ee058ea5760",
			"url": "https://d34o8swod1owfl.cloudfront.net/Report_47700__GET_MERCHANT_LISTINGS_ALL_DATA_.txt",
		},
	},
}
