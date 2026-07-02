/**
 * Main entry: the forwarding primitive plus the subscriber pieces as plain
 * importable values (e.g. for wrapping the forward in a Medusa workflow).
 *
 * Medusa v2 only auto-registers subscribers found in the *app's*
 * `src/subscribers/` directory — an npm package cannot register one by itself.
 * To activate the subscriber, keep a thin file in your app:
 *
 *   // src/subscribers/erpnext-sync.ts
 *   export { default, config } from "@the-groots/medusa-erpnext-subscriber/subscriber"
 */
export { forwardToErpnext } from "./lib/forward"
export { default as erpnextSync, config as erpnextSyncConfig } from "./subscribers/erpnext-sync"
