---
doc_id: kb-06
title: Database Schema, Grain and Join Structure
scope: schema
topic: data model
confidence: high (verified against the live card_analytics schema on 2026-09-23)
source: project schema (card_analytics)
last_updated: 2026-09-23
---

# Schema and grain

## Grain
One row in the fact table = **one authorization attempt**, successful or not. Not customer-level, not order-level, not settlement-level. Retries of the same purchase appear as separate rows, and nothing in the data links a retry to the attempt it repeats.

## Fact table: `card_txns`
`txn_id`, `amt`, `date`, `issuer_id`, `acquirer_id`, `merchant_id`, `customer_id`, `card_id`, `BIN`, `country`, `location`, `response_code`.

- `amt` — amount in rupees (₹), present on declined rows too, where it is attempted rather than realized money
- `date` — a `'YYYY-MM-DD'` string, not a date type; the month key is `SUBSTRING(date,1,7)`
- `response_code` — the only source of outcome; there is no outcome flag on the fact table
- `customer_id`, `card_id` — **masked identifiers**, safe to group and count by
- `country`, `location` — denormalized onto the fact row, so neither needs a join

## Dimension tables
**`issuer_master`** (`id`, `iss_name`) — one row per issuing bank.

**`acquirer_master`** (`id`, `acq_name`) — one row per acquirer. Acquirers are not all banks; processors and aggregators appear here too.

**`merchant_master`** (`merchant_id`, `acquirer_id`, `name`, `mcc_code`, `mcc_description`) — one row per merchant. **Each merchant belongs to exactly one acquirer in this dataset**, so grouping by merchant and by acquirer together does not multiply rows, and an acquirer-level pattern may really be a merchant-level one.

**`BIN_master`** (`BIN`, `card_type`, `issuer_id`, `card_variant`) — the card's product: `card_type` is Credit / Debit / Prepaid, `card_variant` is Classic / Business / Platinum. Any question about card type or variant needs this join.

**`card_master`** (`card_id`, `customer_id`, `BIN`) — one row per issued card, whether or not it ever transacted. It is the denominator of the active-card rate: active cards ÷ all issued cards.

**`response_master`** (`response_code`, `response_description`, `TD_BD`) — one row per response code, classified Success / Business Decline / Technical Decline. **Authoritative for code meaning and classification**, ahead of any generic ISO 8583 reference.

**`customer_master`** (`id`, `name`, `phone_number`, `address`) — personal data, and **deliberately unreachable**. It is not in the join registry, the compiler blocks its name, phone and address columns, and the `customer` dimension resolves to the masked `card_txns.customer_id` instead. A question asking who a customer is cannot be answered, by design, and that is a guarantee worth stating rather than an error.

## Join structure
Every join goes from the fact table straight to a dimension; there are no dimension-to-dimension joins. The key names are inconsistent (`id`, `merchant_id`, `response_code`, `BIN`, `card_id`), which is a common source of join errors.

Because outcome lives on `response_master`, **any question about success or declines needs that join**, including ones that only mention revenue: value-style metrics filter to `TD_BD = 'Success'` implicitly.

## What this data does not contain
- **No authorization-to-settlement link**, so nothing about capture, refunds, chargebacks or disputes
- **No retry chain**, so a repeated attempt cannot be told from a fresh purchase; decline counts are attempts, never affected people
- **No channel, terminal or entry mode**, so card-present versus card-not-present cannot be separated
- **No currency column**: every amount is rupees, even for the non-IN countries in `country`
- **No customer attributes**: age, segment, tenure and risk score do not exist here
- **A bounded date range.** Check it before answering anything comparative: if the data covers a single year, year-on-year comparison is impossible and "this year" means the whole dataset. `SHOW volume BY month` shows the span that exists.

## Consequences for interpretation
- **Each issuer × acquirer × merchant combination is a distinct routing path.** A problem isolated to one path disappears when you aggregate away the dimension that identifies it.
- **Amounts are rupees**, so figures in the hundreds of thousands are lakhs-scale; `IN CRORE` and `IN LAKH` only change the display.
- **Groups get small quickly.** Each added dimension multiplies the number of cells while the transactions stay the same, so an issuer-merchant-month cell can hold a handful of attempts and a rate computed on it is noise. See the caveats document.
