"""
connection.py - WITH COMPREHENSIVE STORE 2 LOGGING
Every step is logged so we can trace exactly where it breaks.
Logging only triggers for Store 2 to avoid cluttering logs.
"""

import base64
import functools
import hashlib
import hmac
import json
import traceback

import frappe
from frappe import _
from shopify.resources import Webhook
from shopify.session import Session

from ecommerce_integrations.shopify.constants import (
    API_VERSION,
    EVENT_MAPPER,
    SETTING_DOCTYPE,
    WEBHOOK_EVENTS,
)
from ecommerce_integrations.shopify.utils import create_shopify_log


def log_store2(step, message, store_name=None):
    """Helper function to log only for Store 2."""
    if store_name and store_name != "Store 1":
        frappe.log_error(
            title=f"[STORE2 DEBUG] Step {step}",
            message=f"Store: {store_name}\n\n{message}"
        )


def temp_shopify_session(func):
    """Any function that needs to access shopify api needs this decorator.
    
    Store-aware: Checks for store_name in kwargs or frappe.local.shopify_store_name
    to determine which store's credentials to use.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # no auth in testing
        if frappe.flags.in_test:
            return func(*args, **kwargs)

        setting = frappe.get_doc(SETTING_DOCTYPE)
        if setting.is_enabled():
            # Determine which store's credentials to use
            store_name = kwargs.get('store_name') or getattr(frappe.local, 'shopify_store_name', None)
            
            # STORE 2 LOGGING
            log_store2("DECORATOR-1", f"""
temp_shopify_session called for function: {func.__name__}
store_name from kwargs: {kwargs.get('store_name')}
store_name from frappe.local: {getattr(frappe.local, 'shopify_store_name', None)}
Final store_name: {store_name}
enable_store_2: {setting.enable_store_2}
""", store_name)
            
            # Use Store 2 credentials if specified and enabled
            if store_name and store_name != "Store 1" and setting.enable_store_2:
                password_2 = setting.get_password("password_2")
                
                log_store2("DECORATOR-2", f"""
Using Store 2 credentials
shopify_url_2: {setting.shopify_url_2}
password_2 exists: {bool(password_2)}
password_2 length: {len(password_2) if password_2 else 0}
""", store_name)
                
                if not password_2:
                    log_store2("DECORATOR-ERROR", "password_2 is None/empty!", store_name)
                    frappe.throw(_("Store 2 API password not configured"))
                
                auth_details = (setting.shopify_url_2, API_VERSION, password_2)
            else:
                # Default to Store 1
                auth_details = (setting.shopify_url, API_VERSION, setting.get_password("password"))

            try:
                log_store2("DECORATOR-3", f"About to create Shopify session with URL: {auth_details[0]}", store_name)
                with Session.temp(*auth_details):
                    log_store2("DECORATOR-4", f"Session created, calling {func.__name__}", store_name)
                    result = func(*args, **kwargs)
                    log_store2("DECORATOR-5", f"Function {func.__name__} completed successfully", store_name)
                    return result
            except Exception as e:
                log_store2("DECORATOR-ERROR", f"""
Exception in temp_shopify_session!
Function: {func.__name__}
Error: {str(e)}
Type: {type(e).__name__}
Traceback:
{traceback.format_exc()}
""", store_name)
                raise

    return wrapper


def register_webhooks(shopify_url: str, password: str) -> list[Webhook]:
    """Register required webhooks with shopify and return registered webhooks."""
    new_webhooks = []

    # clear all stale webhooks matching current site url before registering new ones
    unregister_webhooks(shopify_url, password)

    with Session.temp(shopify_url, API_VERSION, password):
        for topic in WEBHOOK_EVENTS:
            webhook = Webhook.create({"topic": topic, "address": get_callback_url(), "format": "json"})

            if webhook.is_valid():
                new_webhooks.append(webhook)
            else:
                create_shopify_log(
                    status="Error",
                    response_data=webhook.to_dict(),
                    exception=webhook.errors.full_messages(),
                )

    return new_webhooks


def unregister_webhooks(shopify_url: str, password: str) -> None:
    """Unregister all webhooks from shopify that correspond to current site url."""
    url = get_current_domain_name()

    with Session.temp(shopify_url, API_VERSION, password):
        for webhook in Webhook.find():
            if url in webhook.address:
                webhook.destroy()


def get_current_domain_name() -> str:
    """Get current site domain name."""
    if frappe.conf.developer_mode and frappe.conf.localtunnel_url:
        return frappe.conf.localtunnel_url
    else:
        return frappe.request.host


def get_callback_url() -> str:
    """Shopify calls this url when new events occur to subscribed webhooks."""
    url = get_current_domain_name()
    return f"https://{url}/api/method/ecommerce_integrations.shopify.connection.store_request_data"


@frappe.whitelist(allow_guest=True)
def store_request_data() -> None:
    """
    Central webhook endpoint for all Shopify stores.
    Routes webhooks to the correct handler based on shop domain.
    """
    
    # =========================================================================
    # STEP 1: Initial request validation
    # =========================================================================
    if not frappe.request:
        frappe.log_error(
            title="[STORE2 DEBUG] Step 1 - FAILED",
            message="No frappe.request object!"
        )
        frappe.throw(_("Invalid request"))
        return
    
    # Get headers early for logging
    shop_domain = frappe.get_request_header("X-Shopify-Shop-Domain")
    event = frappe.get_request_header("X-Shopify-Topic")
    hmac_header = frappe.get_request_header("X-Shopify-Hmac-Sha256")
    
    # Check if this MIGHT be Store 2 (for early logging)
    setting = frappe.get_doc(SETTING_DOCTYPE)
    is_likely_store2 = (
        setting.enable_store_2 and 
        setting.shopify_url_2 and 
        shop_domain and 
        shop_domain in setting.shopify_url_2
    )
    
    if is_likely_store2:
        frappe.log_error(
            title="[STORE2 DEBUG] Step 1 - Request Received",
            message=f"""
======================================
STORE 2 WEBHOOK RECEIVED
======================================
shop_domain: {shop_domain}
event: {event}
hmac_header exists: {bool(hmac_header)}
hmac_header length: {len(hmac_header) if hmac_header else 0}
hmac_header (first 20): {hmac_header[:20] if hmac_header else 'NONE'}...
request.data length: {len(frappe.request.data) if frappe.request.data else 0}
request.data (first 200): {frappe.request.data[:200] if frappe.request.data else 'NONE'}
======================================
"""
        )
    
    # =========================================================================
    # STEP 2: Validate shop_domain header
    # =========================================================================
    if not shop_domain:
        if is_likely_store2:
            frappe.log_error(
                title="[STORE2 DEBUG] Step 2 - FAILED",
                message="shop_domain header is missing!"
            )
        frappe.throw(_("Missing shop domain header"))
        return
    
    if is_likely_store2:
        frappe.log_error(
            title="[STORE2 DEBUG] Step 2 - shop_domain OK",
            message=f"shop_domain: {shop_domain}"
        )
    
    # =========================================================================
    # STEP 3: Determine which store
    # =========================================================================
    store_name = None
    shared_secret = None
    
    # Check Store 1
    if setting.shopify_url and shop_domain in setting.shopify_url:
        store_name = "Store 1"
        shared_secret = setting.shared_secret
    
    # Check Store 2
    elif setting.enable_store_2 and setting.shopify_url_2 and shop_domain in setting.shopify_url_2:
        store_name = setting.store_2_name or "Store 2"
        shared_secret = setting.shared_secret_2
        
        frappe.log_error(
            title="[STORE2 DEBUG] Step 3 - Store Matched",
            message=f"""
Store identified as: {store_name}
shop_domain: {shop_domain}
shopify_url_2: {setting.shopify_url_2}
shared_secret_2 exists: {bool(shared_secret)}
shared_secret_2 type: {type(shared_secret)}
shared_secret_2 length: {len(shared_secret) if shared_secret else 0}
shared_secret_2 (first 10): {shared_secret[:10] if shared_secret else 'NONE'}...
"""
        )
    
    # Unknown store
    else:
        frappe.log_error(
            title="[STORE2 DEBUG] Step 3 - NO MATCH",
            message=f"""
shop_domain: {shop_domain}
Store 1 URL: {setting.shopify_url}
Store 2 URL: {setting.shopify_url_2}
Store 2 Enabled: {setting.enable_store_2}
"""
        )
        frappe.throw(_(f"Unknown Shopify store: {shop_domain}"))
        return
    
    # =========================================================================
    # STEP 4: Validate shared_secret exists
    # =========================================================================
    if store_name != "Store 1":
        frappe.log_error(
            title="[STORE2 DEBUG] Step 4 - Validating shared_secret",
            message=f"""
shared_secret is None: {shared_secret is None}
shared_secret is empty string: {shared_secret == ''}
shared_secret is falsy: {not shared_secret}
bool(shared_secret): {bool(shared_secret)}
repr(shared_secret): {repr(shared_secret)[:50] if shared_secret else 'None'}
"""
        )
    
    if not shared_secret:
        log_store2("4-FAILED", "shared_secret is None or empty!", store_name)
        frappe.throw(_(f"Shared secret not configured for {store_name}"))
        return
    
    log_store2("4-OK", "shared_secret exists and is not empty", store_name)
    
    # =========================================================================
    # STEP 5: Validate HMAC header exists
    # =========================================================================
    log_store2("5", f"""
Checking HMAC header...
hmac_header exists: {bool(hmac_header)}
hmac_header is None: {hmac_header is None}
hmac_header type: {type(hmac_header)}
""", store_name)
    
    if not hmac_header:
        log_store2("5-FAILED", "hmac_header is None or empty!", store_name)
        frappe.throw(_("Missing HMAC signature header"))
        return
    
    log_store2("5-OK", f"HMAC header present: {hmac_header[:20]}...", store_name)
    
    # =========================================================================
    # STEP 6: HMAC Validation
    # =========================================================================
    log_store2("6", f"""
About to validate HMAC...
shared_secret type: {type(shared_secret)}
shared_secret length: {len(shared_secret)}
hmac_header type: {type(hmac_header)}
hmac_header length: {len(hmac_header)}
request.data type: {type(frappe.request.data)}
request.data length: {len(frappe.request.data)}
""", store_name)
    
    try:
        log_store2("6a", "Calling shared_secret.encode('utf8')...", store_name)
        secret_bytes = shared_secret.encode("utf8")
        log_store2("6b", f"Success! secret_bytes length: {len(secret_bytes)}", store_name)
        
        log_store2("6c", "Computing HMAC...", store_name)
        computed_hmac = hmac.new(secret_bytes, frappe.request.data, hashlib.sha256)
        log_store2("6d", "HMAC computed, getting digest...", store_name)
        
        digest = computed_hmac.digest()
        log_store2("6e", f"Digest obtained, length: {len(digest)}", store_name)
        
        sig = base64.b64encode(digest)
        log_store2("6f", f"Base64 encoded sig: {sig[:30]}...", store_name)
        
        log_store2("6g", "Encoding hmac_header to bytes...", store_name)
        expected_sig = bytes(hmac_header.encode())
        log_store2("6h", f"Expected sig: {expected_sig[:30]}...", store_name)
        
        log_store2("6i", f"""
Comparing signatures...
Computed: {sig}
Expected: {expected_sig}
Match: {sig == expected_sig}
""", store_name)
        
        if sig != expected_sig:
            log_store2("6-FAILED", f"""
HMAC MISMATCH!
Computed: {sig}
Expected: {expected_sig}

This means the shared_secret in ERPNext doesn't match Shopify's signing key.
Check: Shopify Admin → Settings → Notifications → Webhooks → "Your webhooks will be signed with: XXX"
""", store_name)
            create_shopify_log(status="Error", request_data=frappe.request.data)
            frappe.throw(_("Unverified Webhook Data"))
            return
        
        log_store2("6-OK", "HMAC validation PASSED!", store_name)
        
    except AttributeError as e:
        log_store2("6-EXCEPTION", f"""
AttributeError during HMAC validation!
Error: {str(e)}
This usually means shared_secret or hmac_header is None.

shared_secret is None: {shared_secret is None}
hmac_header is None: {hmac_header is None}

Traceback:
{traceback.format_exc()}
""", store_name)
        raise
        
    except Exception as e:
        log_store2("6-EXCEPTION", f"""
Exception during HMAC validation!
Error type: {type(e).__name__}
Error: {str(e)}

Traceback:
{traceback.format_exc()}
""", store_name)
        raise
    
    # =========================================================================
    # STEP 7: Parse JSON data
    # =========================================================================
    log_store2("7", "Parsing JSON data...", store_name)
    
    try:
        data = json.loads(frappe.request.data)
        log_store2("7-OK", f"""
JSON parsed successfully!
Order ID: {data.get('id', 'N/A')}
Order Number: {data.get('name', 'N/A')}
Customer: {data.get('customer', {}).get('email', 'N/A') if data.get('customer') else 'N/A'}
Total Price: {data.get('total_price', 'N/A')}
Line Items Count: {len(data.get('line_items', []))}
""", store_name)
    except json.JSONDecodeError as e:
        log_store2("7-FAILED", f"""
JSON parse error!
Error: {str(e)}
Raw data (first 500): {frappe.request.data[:500]}
""", store_name)
        frappe.throw(_("Invalid JSON in webhook payload"))
        return
    
    # =========================================================================
    # STEP 8: Validate event type
    # =========================================================================
    log_store2("8", f"""
Validating event type...
Event: {event}
Event in EVENT_MAPPER: {event in EVENT_MAPPER}
Available events: {list(EVENT_MAPPER.keys())}
""", store_name)
    
    if not event:
        log_store2("8-FAILED", "Event header is missing!", store_name)
        frappe.throw(_("Missing webhook event type"))
        return
    
    if event not in EVENT_MAPPER:
        log_store2("8-FAILED", f"Event '{event}' not in EVENT_MAPPER!", store_name)
        frappe.throw(_(f"Unsupported webhook event: {event}"))
        return
    
    log_store2("8-OK", f"Event '{event}' is valid, maps to: {EVENT_MAPPER[event]}", store_name)
    
    # =========================================================================
    # STEP 9: Set store context
    # =========================================================================
    log_store2("9", f"Setting frappe.local.shopify_store_name = '{store_name}'", store_name)
    frappe.local.shopify_store_name = store_name
    log_store2("9-OK", f"Store context set. Verified: {frappe.local.shopify_store_name}", store_name)
    
    # =========================================================================
    # STEP 10: Call process_request
    # =========================================================================
    log_store2("10", f"""
About to call process_request()
Event: {event}
Store: {store_name}
Order ID: {data.get('id')}
""", store_name)
    
    try:
        process_request(data, event, store_name)
        log_store2("10-OK", "process_request() completed without exception", store_name)
    except Exception as e:
        log_store2("10-EXCEPTION", f"""
Exception in process_request!
Error: {str(e)}
Type: {type(e).__name__}

Traceback:
{traceback.format_exc()}
""", store_name)
        raise


def process_request(data, event, store_name=None):
    """Process webhook request with store context."""
    
    # =========================================================================
    # STEP 11: Inside process_request
    # =========================================================================
    log_store2("11", f"""
Inside process_request()
Event: {event}
Store: {store_name}
Order ID: {data.get('id')}
Method to call: {EVENT_MAPPER[event]}
""", store_name)
    
    # =========================================================================
    # STEP 11.5: Early filter for noisy `orders/updated` webhooks
    # =========================================================================
    # NOTE: Per business requirements, this fingerprint ONLY tracks:
    # - Shipping address
    # - Billing address
    # - Order notes
    #
    # Line items are handled via the dedicated `orders/edited` webhook, so they
    # are *not* part of this fingerprint. This means:
    # - Old/timeline/metadata-only updates on untouched orders are dropped here.
    # - Only REAL address / note changes for existing Sales Orders will proceed.
    if event == "orders/updated":
        try:
            from ecommerce_integrations.shopify.constants import ORDER_ID_FIELD
            
            order_id = str(data.get("id") or "")
            so_name = None
            if order_id:
                so_name = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: order_id}, "name")
            
            # If we have a matching Sales Order, compare fingerprints
            if so_name:
                new_fingerprint = _build_order_fingerprint(data)
                
                try:
                    old_fingerprint = frappe.db.get_value(
                        "Sales Order", so_name, "custom_shopify_fingerprint"
                    ) or ""
                except Exception:
                    # If the field doesn't exist yet or any error occurs, skip fingerprint filter
                    log_store2(
                        "11.5-SKIP",
                        f"Fingerprint field missing or error while reading for SO {so_name}. "
                        f"Proceeding without early-exit filter.",
                        store_name,
                    )
                    old_fingerprint = ""
                
                # If fingerprint unchanged, drop this webhook before logging / enqueue
                if old_fingerprint and new_fingerprint == old_fingerprint:
                    log_store2(
                        "11.5-SKIP",
                        f"orders/updated fingerprint UNCHANGED for order_id={order_id}, so_name={so_name}. "
                        f"Skipping log + enqueue to avoid noise.",
                        store_name,
                    )
                    return
                
                # Fingerprint changed or first time: update it so future metadata-only
                # webhooks on the same order can be skipped.
                try:
                    frappe.db.set_value(
                        "Sales Order",
                        so_name,
                        "custom_shopify_fingerprint",
                        new_fingerprint,
                        update_modified=False,
                    )
                    frappe.db.commit()
                    log_store2(
                        "11.5-SET",
                        f"Updated fingerprint for SO {so_name}. "
                        f"Old: {old_fingerprint or 'EMPTY'} | New: {new_fingerprint}",
                        store_name,
                    )
                except Exception as e:
                    log_store2(
                        "11.5-SET-ERROR",
                        f"Failed to update fingerprint for SO {so_name}. Error: {str(e)}",
                        store_name,
                    )
        except Exception as e:
            # Fail open: if anything goes wrong in fingerprint logic, we still
            # want the webhook to be processed normally.
            log_store2(
                "11.5-EXCEPTION",
                f"Exception in orders/updated fingerprint filter: {str(e)}\n"
                f"Traceback:\n{traceback.format_exc()}",
                store_name,
            )
    
    # =========================================================================
    # STEP 12: Create Shopify log
    # =========================================================================
	log_store2("12", "Creating Shopify log entry...", store_name)
	
	try:
		log = create_shopify_log(method=EVENT_MAPPER[event], request_data=data)
		log_store2("12-OK", f"Log created: {log.name}", store_name)
	except Exception as e:
		log_store2("12-EXCEPTION", f"""
Failed to create Shopify log!
Error: {str(e)}

Traceback:
{traceback.format_exc()}
""", store_name)
		raise
	
	# =========================================================================
	# STEP 13: Enqueue background job
	# =========================================================================
	log_store2("13", f"""
About to enqueue background job...
Method: {EVENT_MAPPER[event]}
Queue: short
Timeout: 300
Kwargs: payload (order data), request_id={log.name}, store_name={store_name}
""", store_name)
	
	try:
		frappe.enqueue(
			method=EVENT_MAPPER[event],
			queue="short",
			timeout=300,
			is_async=True,
			**{"payload": data, "request_id": log.name, "store_name": store_name},
		)
		log_store2("13-OK", f"""
Job enqueued successfully!
Method: {EVENT_MAPPER[event]}
Log ID: {log.name}
Store: {store_name}

The webhook handler has completed. 
The background worker should now pick up the job.
Check RQ Job doctype for the job status.
""", store_name)
	except Exception as e:
		log_store2("13-EXCEPTION", f"""
Failed to enqueue job!
Error: {str(e)}

Traceback:
{traceback.format_exc()}
""", store_name)
		raise


def _build_order_fingerprint(data):
	"""Build a fingerprint of ONLY the fields we care about for `orders/updated`.
	
	Per the current requirement, this fingerprint includes:
	- Order note
	- Billing address (core fields)
	- Shipping address (core fields)
	
	Line items and other metadata are intentionally NOT included here. Line-item
	changes are handled via the dedicated `orders/edited` webhook instead.
	"""
	import hashlib
	import json
	
	fingerprint_data = {
		"note": data.get("note") or "",
		"billing_address": _address_hash(data.get("billing_address")),
		"shipping_address": _address_hash(data.get("shipping_address")),
	}
	
	raw = json.dumps(fingerprint_data, sort_keys=True)
	return hashlib.md5(raw.encode()).hexdigest()


def _address_hash(address):
	"""Return a stable string representing the address fields we care about."""
	if not address:
		return ""
	
	return "|".join(
		[
			str(address.get("address1") or "").strip(),
			str(address.get("address2") or "").strip(),
			str(address.get("city") or "").strip(),
			str(address.get("province") or "").strip(),
			str(address.get("zip") or "").strip(),
			str(address.get("country") or "").strip(),
			str(address.get("phone") or "").strip(),
		]
	)


def _validate_request(req, hmac_header, shared_secret):
    """Validate Shopify webhook using HMAC.
    
    Note: This function is now bypassed - validation is done inline in store_request_data
    with detailed logging. Keeping for backward compatibility.
    """
    sig = base64.b64encode(hmac.new(shared_secret.encode("utf8"), req.data, hashlib.sha256).digest())

    if sig != bytes(hmac_header.encode()):
        create_shopify_log(status="Error", request_data=req.data)
        frappe.throw(_("Unverified Webhook Data"))