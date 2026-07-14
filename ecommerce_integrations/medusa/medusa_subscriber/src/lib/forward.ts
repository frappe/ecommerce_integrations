import crypto from "crypto"

/**
 * Forward a Medusa event to the ERPNext `ecommerce_integrations` connector.
 *
 * Mirrors the contract enforced by
 * `ecommerce_integrations.medusa.connection.store_request_data`:
 *
 *   - The request body is the JSON-stringified payload (the *full* Medusa object).
 *   - `X-Medusa-Hmac-Sha256` is the base64 of HMAC-SHA256(body, MEDUSA_ERPNEXT_WEBHOOK_SECRET).
 *     ERPNext recomputes this over the raw bytes it receives, so we must POST the
 *     exact same string we signed.
 *   - `X-Medusa-Topic` is the Medusa event name (one of the EVENT_MAPPER topics).
 *
 * @param topic   Medusa event name, e.g. "order.placed".
 * @param payload The full object to forward (already fetched from the module service).
 */
export async function forwardToErpnext(topic: string, payload: unknown): Promise<void> {
  const url = process.env.MEDUSA_ERPNEXT_URL
  const secret = process.env.MEDUSA_ERPNEXT_WEBHOOK_SECRET

  if (!url) {
    console.error("[erpnext-sync] MEDUSA_ERPNEXT_URL is not set; skipping forward of", topic)
    return
  }
  if (!secret) {
    console.error("[erpnext-sync] MEDUSA_ERPNEXT_WEBHOOK_SECRET is not set; skipping forward of", topic)
    return
  }

  // Sign exactly what we send: the body string is both signed and POSTed.
  const body = JSON.stringify(payload)
  const signature = crypto.createHmac("sha256", secret).update(body, "utf8").digest("base64")

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Medusa-Topic": topic,
        "X-Medusa-Hmac-Sha256": signature,
      },
      body,
    })

    if (!res.ok) {
      const text = await res.text().catch(() => "<no body>")
      console.error(
        `[erpnext-sync] ERPNext rejected ${topic}: HTTP ${res.status} ${res.statusText} — ${text}`
      )
      // Throw so a wrapping Medusa workflow (if used) can retry/compensate.
      throw new Error(`ERPNext forward failed for ${topic}: HTTP ${res.status}`)
    }
  } catch (err) {
    console.error(`[erpnext-sync] failed to forward ${topic} to ERPNext:`, err)
    throw err
  }
}
