# Billing + Wallet Schema (2026-09-08)

Jenna 2026-09-08 (verbatim): *"I want to enter something where people
can either sign up online for the dashboard and pay a fee that is
charged to their credit card or can buy additional credits with their
credit card or where an admin can put a credit card in and it charges
their prometheus charges to it."*

## Single-wallet model (Anthropic-style)

One dollar balance per user. No "credits" abstraction on the customer
side. Admin sets per-tool USD prices. Every pull deducts the tool's
USD price. Prometheus analysis sessions deduct metered Anthropic cost
x 2.10 in real time. Same balance covers everything.

## users.json fields (per user, all additive)

Existing fields are UNCHANGED. New fields (all optional, default to
"not a paying customer" behavior):

```json
{
  "wallet_balance_usd": 0.0,                    // current $ balance
  "wallet_lifetime_topups_usd": 0.0,            // audit total
  "wallet_lifetime_spend_usd": 0.0,             // audit total
  "wallet_transactions": [                      // last 500 entries
    {
      "ts": "2026-09-08T20:15:00Z",
      "kind": "topup" | "deduct" | "refund" | "adjustment",
      "amount_usd": 500.00,                     // positive = credit,
                                                // negative = debit
      "balance_after_usd": 500.00,
      "description": "Prepay pack (Stripe)",
      "job_id": "...",                          // when applicable
      "tool": "profile_iq" | "prometheus" | "",
      "stripe_ref": "cs_..." | "pi_..." | ""
    }
  ],

  "paying_customer": false,                     // shows wallet UI
  "internal_allowance_drains_first": true,     // existing 'credits'
                                                // field drains before
                                                // wallet. Default true
                                                // for backwards compat.

  "stripe_customer_id": "cus_...",             // set on first save
  "stripe_payment_method_id": "pm_...",        // saved card
  "stripe_payment_method_last4": "4242",       // display only
  "stripe_payment_method_brand": "visa",       // display only

  "billing_mode": "prepay_only"
                  | "auto_reload"
                  | "monthly_invoice",         // default prepay_only

  "auto_reload_threshold_usd": 500.0,          // when balance < X ...
  "auto_reload_amount_usd": 1000.0,            // ... charge Y

  "monthly_invoice_limit_usd": 5000.0,         // wallet can run to
                                                // this negative. On
                                                // the 1st, Stripe
                                                // charges (0 - bal).
  "last_monthly_invoice_ts": null              // most recent charge
}
```

## Deduction order (new logic in consume_credit)

For every pull that costs $X:

1. **If `credits` (internal allowance) is >= X_credits_equivalent:**
   Existing path unchanged. Uses `credits` field. No wallet touch.
   (Internal Crosswalk users, pre-allocated grants.)
2. **Else if `paying_customer=true`:**
   Deduct $X from `wallet_balance_usd`. If wallet balance goes below
   zero:
   - `billing_mode='auto_reload'`: trigger a Stripe charge for
     `auto_reload_amount_usd`; if it succeeds, wallet is topped up
     and the pull proceeds. If it fails, block the pull, email admin.
   - `billing_mode='monthly_invoice'`: allow wallet to go negative
     up to `monthly_invoice_limit_usd`. Beyond the limit, block.
   - `billing_mode='prepay_only'`: block. Show "Top up" screen.
3. **Else (not a paying customer, no internal credits):**
   Existing behavior. Fails the pull with "insufficient credits."

## Pricing config (system/pricing.json in S3)

```json
{
  "per_tool_usd": {
    "profile_iq_build": 500.0,
    "profile_iq_derived_cut": 100.0,
    "subscriber_iq_build": 1000.0,
    "chatbot_profile_iq_build": 500.0,
    "digital_journey_iq": 0.0,
    "impact_iq": 0.0,
    "attribution_iq": 0.0
  },
  "top_up_packs_usd": [250, 500, 1000, 2500],
  "top_up_min_custom_usd": 100.0,
  "prometheus_markup_multiplier": 2.10,
  "auto_reload_defaults": {
    "threshold_usd": 500.0,
    "amount_usd": 1000.0
  },
  "monthly_invoice_defaults": {
    "limit_usd": 5000.0
  }
}
```

## Stripe objects created per paying customer

- **Customer** (`cus_...`) - one per dashboard user, on first billing
  interaction. Email + name from `users.json`. Stored as
  `stripe_customer_id`.
- **PaymentMethod** (`pm_...`) - saved card. Stored as
  `stripe_payment_method_id` after admin attaches it via Stripe
  Elements SetupIntent.
- **Checkout Session** (`cs_...`) - one-time prepay pack purchases.
  Wallet topped up on `checkout.session.completed` webhook.
- **PaymentIntent** (`pi_...`) - auto-reload charges and admin
  custom charges. Confirmed off-session against the saved payment
  method.
- **Invoice** (`in_...`) - monthly-invoice mode. Created + finalized
  + charged by our monthly cron.

## Environment variables

- `STRIPE_ENABLED` - `true` / `false`. When false, all billing calls
  no-op. Admin UI shows "Billing not configured." Default false.
- `STRIPE_SECRET_KEY` - `sk_live_...` or `sk_test_...`
- `STRIPE_PUBLISHABLE_KEY` - `pk_live_...` or `pk_test_...`
- `STRIPE_WEBHOOK_SECRET` - `whsec_...` used to verify webhook
  signatures.

## Webhook events subscribed

- `checkout.session.completed` - one-time prepay top-up landed.
- `payment_intent.succeeded` - auto-reload or admin custom charge.
- `payment_intent.payment_failed` - card decline. Email admin.
- `charge.refunded` - refund landed. Deduct from wallet.
- `customer.updated` - card update (Stripe updater service).
- `invoice.paid` - monthly invoice settled.
- `invoice.payment_failed` - monthly invoice declined.

## Idempotency

Webhook events dedupe by `event.id` stored in
`s3://<bucket>/system/billing/stripe_events_processed.json`
(atomic CAS via `s3_json_state`). A retry of an already-processed
event is a no-op.

Wallet deductions inside `consume_credit` are wrapped in the
existing `_users_cas_mutate` (see `bg-webapp/app.py`), so an
external race that changed the balance after our read is folded
in on retry rather than clobbered.

## Backwards compatibility

- Existing users have no wallet fields -> `paying_customer=false`
  by default -> wallet UI hidden -> zero behavior change.
- Existing `credits` field is untouched. It drains first (per
  `internal_allowance_drains_first=true`).
- Super admins keep their `credits=-1` unlimited-internal
  behavior. The wallet UI is hidden for them unless they
  toggle `paying_customer=true` on themselves.

## No modeled / no internal-jargon language

Every user-facing surface (buttons, screens, email bodies) says
"wallet" / "balance" / "top up" / "Add funds" / "Card on file" -
never "Stripe" (except in the tiny attribution "Powered by Stripe"
badge Stripe requires), never "PaymentIntent" / "SetupIntent" /
"webhook" / "cus_..." / "pm_...". Per `no-modeled-or-source-language.mdc`.
