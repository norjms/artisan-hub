# Stores: WooCommerce, Shopify and Etsy

The hub sells through any mix of WooCommerce, Shopify and Etsy, and does the same work for each:

- **Products:** "Push to stores" lists the product on every connected store it sells on, as a draft until it goes
  live. The Live switch shows or hides it everywhere. On the product page each store has its own row with a switch
  to keep the product off that store.
- **Stock:** one shared pool. Every store gets the same number: units buildable from the supplies on hand, minus
  every open order from any store, capped by the product's stock cap. It is pushed when it changes and every five
  minutes.
- **Orders:** each store's webhooks bring orders in, and a check every ten minutes catches anything missed. Paid
  orders go to the farm the same way for every store (order routing on the Farm page).
- **Shipping:** "Mark shipped" on the Orders page completes the order in WooCommerce, fulfils it in Shopify or marks
  it shipped on Etsy, with tracking if you give it.

Order statuses are shown in the hub's terms:

| Hub status | Shopify | Etsy |
| --- | --- | --- |
| pending | payment pending | payment processing |
| on-hold | authorized, partially paid | |
| processing | paid, not yet fulfilled | paid |
| completed | fulfilled | shipped or completed |
| cancelled | cancelled | canceled |
| refunded | refunded or voided | fully refunded |

## Shopify

1. In the [Shopify Dev Dashboard](https://dev.shopify.com/dashboard), create an app for your store. Give it these
   Admin API scopes:
   - `read_products`, `write_products`
   - `read_inventory`, `write_inventory`
   - `read_locations`
   - `read_orders`, `write_orders`
   - `read_merchant_managed_fulfillment_orders`, `write_merchant_managed_fulfillment_orders`
   - `read_customers`: optional. Without it, orders come in without the customer's name.
2. Install the app on the store. The app and the store must be in the same organization.
3. In the hub, open Connections, Shopify store, and fill in:
   - the store name (`my-store.myshopify.com`);
   - the app's client ID and client secret.

   The hub trades them for an access token and renews it every day.
4. On Sync health, run "Set up connected apps". It registers the order webhooks, which Shopify signs with the
   app's client secret.

A store that still has a custom app from before 2026 can enter that app's Admin API access token (`shpat_...`),
with the app's API secret as the webhook secret, instead of a client ID and secret.

Stock goes to the store's main location, or to the location id you enter.

## Etsy

1. At [etsy.com/developers/your-apps](https://www.etsy.com/developers/your-apps), create an app. Note its keystring
   and shared secret.
2. Add the hub's callback address as the app's redirect URI. The Etsy connection dialog shows it:
   `https://<hub>/oauth/etsy/callback`.
3. In the hub, open Connections, Etsy shop, enter the keystring and shared secret, and save.
4. Click Connect Etsy and approve the hub in the shop owner's Etsy account. The hub keeps itself signed in after
   that. If it is ever signed out (Etsy ends a sign-in that goes unused for 90 days), click Connect Etsy again.
5. Edit the Etsy connection again and pick the listing defaults that Etsy requires. Once the shop is connected,
   the shipping profile, return policy and processing time are lists to choose from.
   - Etsy category (taxonomy id)
   - shipping profile
   - processing time (readiness state)
   - who made it and when
   - return policy (optional)
6. In the Webhook portal for your app, add the endpoint `https://<hub>/hooks/etsy` for `order.paid`,
   `order.canceled` and `order.shipped`. Copy its signing secret (`whsec_...`) into the Etsy connection's webhook
   secret.

How products appear on Etsy:

- **Colors:** each color is a variation with its own stock, price and SKU.
- **Description:** plain text, followed by the designer credit when the license asks for one.
- **Title and tags:** cut to Etsy's limits.
- **Pictures:** the first ten web pictures.
- **Sale price:** Etsy runs sales through its own promotions, so the hub sends the regular price only.

Etsy limits apps to 10 requests a second and 10,000 a day. The hub sends stock only when it changes.
