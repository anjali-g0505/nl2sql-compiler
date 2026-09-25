---
doc_id: kb-01
title: Card Payment Participants — Entity Glossary
scope: definitions
topic: entities
confidence: high (general industry definitions); entity-to-column mapping = project-specific
source: general payments industry knowledge (four-party model)
last_updated: 2026-09-23
---

# Card Payment Participants

## The four-party model
A card transaction involves four core parties plus the network that connects them. Every row in the transaction table is the record of one such interaction.

**Cardholder** — the person paying. Holds a card issued by the issuer. Not stored as a dimension in this database (transactions are aggregated, not customer-level).

**Issuer (issuing bank)** — the bank that issued the card to the cardholder, holds the account, and makes the final approve/decline decision on each authorization. The issuer is the party that emits the response code. In this database: the `issuer_master` dimension; `SHOW volume BY issuer` lists the current set.

**Merchant** — the business accepting the payment. Identified by a merchant ID and a merchant category. In this database: `merchant_master`.

**Acquirer (acquiring bank)** — the bank or processor that holds the merchant's account, accepts the transaction on the merchant's behalf, and routes it into the network. In this database: the `acquirer_master` dimension; `SHOW volume BY acquirer` lists the current set.

**Card network / switch** — the rails between acquirer and issuer (RuPay, Visa, Mastercard). The network routes the authorization request to the correct issuer and routes the response back. When the network itself cannot route or the issuer does not answer, the network may generate the response code instead of the issuer.

## Distinctions that matter for analysis
- **Issuer ≠ acquirer.** The same bank can appear in both lists for different transactions. A bank named as issuer means "that bank's cardholder paid"; the same name as acquirer means "the merchant banks there". Never conflate the two when attributing a problem, and check which dimension a question actually names.
- **Acquirers are not always banks.** Payment processors and aggregators occupy the acquiring position without being a bank, and appear in the acquirer dimension alongside banks. They behave the same way in the data.
- **Responsibility for a decline usually sits with the issuer**, because the issuer makes the authorization decision. Exceptions are routing and switch failures, which sit with the network or acquirer side.

## Related terms
- **Authorization** — the real-time approve/decline decision. This database records authorizations, not settlement.
- **Settlement** — the later movement of funds. Not in scope here.
- **MCC (merchant category code)** — classifies what a merchant sells; drives some issuer decline rules.
- **BIN** — the leading digits of a card number identifying the issuer.

## The participants in this system
The names are data, not knowledge, so they are not listed here — `SHOW volume BY issuer`, `BY acquirer` or `BY merchant` returns the current set with their sizes. What is stable is the structure:

- **Issuers, acquirers and merchants are separate dimensions**, and a question names one of them. "Declines at bank X" means X's *cardholders* were declined, wherever they shopped; the same name in the acquirer dimension would be a different population entirely.
- **A merchant belongs to one acquirer.** An acquirer-level figure is therefore the sum of its merchants and can be produced by a single one of them, so drill from acquirer to merchant before attributing anything to the acquirer's platform. Confirm the relationship holds for the data at hand with `SHOW volume BY acquirer, merchant`.
- **Cardholders and cards appear as masked ids only.** They can be counted and grouped, never identified — see kb-06.
- **There is no network or scheme dimension.** Technical declines are attributable to the issuer side or the switch as a group, never to a named network.
