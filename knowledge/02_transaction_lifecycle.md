---
doc_id: kb-02
title: Authorization Lifecycle — How a Response Code Is Produced
scope: process
topic: transaction flow
confidence: high (general industry process)
source: general payments industry knowledge (ISO 8583 authorization flow)
last_updated: 2026-09-23
---

# How an authorization produces a response code

## The path of one transaction
1. **Cardholder presents the card** at a merchant (in store, online, or in-app).
2. **Merchant sends the request to its acquirer**, usually through a terminal, gateway, or aggregator.
3. **Acquirer formats the request** as an authorization message and sends it into the card network.
4. **Network routes the request to the issuer** identified by the card's BIN.
5. **Issuer decides**: checks the account exists and is active, checks available balance or credit, applies limits and velocity rules, applies fraud and risk scoring.
6. **Issuer returns a response code** — a two-character code carried in field 39 of the ISO 8583 authorization response.
7. **Network and acquirer relay the code back** to the merchant, which either completes or fails the sale.

## Where the code actually comes from
The response code is not always the issuer's decision:
- **Issuer-generated** — the normal case. The issuer evaluated the transaction and said yes or no with a reason.
- **Network/switch-generated** — when the issuer is unreachable, times out, or cannot be identified, the network returns a code on the issuer's behalf (codes such as issuer or switch inoperative, or financial institution not found). These are infrastructure outcomes, not credit decisions.
- **Acquirer or gateway-generated** — formatting and validation failures can be rejected before ever reaching the issuer.

This matters for analysis: a spike in issuer-generated declines is a customer or risk-policy story; a spike in network-generated declines is an outage or routing story. The same issuer name appears on both, so the response code is the only way to tell them apart.

## What the data records
Each row in the transaction fact table is one authorization attempt with its final response code. Consequences:
- **A declined transaction is still a row.** It carries an amount even though no money moved.
- **Retries appear as separate rows.** One customer failing twice and succeeding on the third try is three rows. Decline counts therefore overstate the number of affected customers.
- **Only approved transactions represent realized revenue.** Declined rows carry an attempted amount, not an earned amount.

## Which steps this system can actually see
The lifecycle above has seven steps; the data records the outcome of the whole chain and nothing in between.

**Visible:** the acquirer, the issuer, the final response code, the amount, the merchant, the card product, and where the attempt happened.

**Not visible:** the terminal or channel, whether the card was present, the time of day (dates only), any latency, and any link between an attempt and the retry that follows it.

**How to tell the origins apart in practice.** The response code is the only evidence of which step produced the outcome, so the first cut is always by class: `SHOW volume BY status` separates approvals, business declines and technical declines, and `SHOW volume BY response WHERE status = 'Technical Decline'` names the infrastructure failure. An issuer refusal (business) and a switch failure (technical) can carry the same issuer name, and nothing but the code distinguishes them.

**One asymmetry worth remembering:** a timeout (`68`) means the switch gave up before the issuer answered. The issuer may have approved a transaction that this data records as declined, so timeouts are a reconciliation question as well as an availability one.
