---
doc_id: kb-03
title: Response Code Reference and Interpretation (ISO 8583 field 39)
scope: reference
topic: response codes
confidence: high for the codes this system holds (the list is verified against response_master); medium for the generic ISO notes on codes not present
source: response_master table, plus public ISO 8583 / card scheme documentation for the interpretation notes
last_updated: 2026-09-25
---

# Response code reference

Response codes are two-character values returned in field 39 of the ISO 8583 authorization response. `00` is the only approval; everything else is a decline or an exception. Networks, issuers and regions interpret some codes differently, so where this document and `response_master` disagree, **`response_master` wins**.

## Which codes exist
The authoritative list is the `response_master` table, not this document and not the ISO standard. It is a small table, so it can simply be queried: `SHOW volume BY response` returns every code that appears in the data, with its description, and `SHOW volume BY response WHERE status != 'Success'` returns the declining ones. The wider industry uses codes this database does not contain; if a question names one of those, the answer is that this system has no such code — not an explanation of what it would have meant elsewhere.

## The codes, and what an elevated share of each one means
The right question is never "how many `05`s are there" but "is `05` a larger share of declines here than it is elsewhere, or than it was before". Run the code breakdown for the group in question and for a comparison group, then read the differences with this table.

| Code | Meaning | Class | If this code is elevated |
|---|---|---|---|
| 00 | Approved | Success | Nothing to diagnose; this is the only code that represents realized revenue |
| 05 | Do not honor | Business | The issuer refused without saying why. An elevated share caps how far any diagnosis can go: it can hide balance, risk scoring or an account block. Report it as undisclosed rather than guessing, and take it to the issuer |
| 51 | Insufficient funds | Business | Cardholder affordability. Look for concentration in higher-ticket transactions, in particular merchants, or at month-end. The lever is retry timing, not the payment platform |
| 54 | Expired card | Business | Stale stored credentials. Expect it where cards are kept on file for recurring billing; a rising share suggests an ageing card-on-file base and an update flow that isn't working |
| 61 | Exceeds withdrawal amount limit | Business | Per-transaction or daily caps being hit. Compare the ticket sizes of declined and approved attempts for the same group; if declines skew large, this is a limit problem, not a balance problem |
| 57 | Transaction not permitted to cardholder | Business | The card is not enabled for this kind of transaction. Concentrated by card product or merchant category rather than spread evenly |
| 41 | Lost card | Business | Card lifecycle. Small everywhere; if it is large at one merchant, suspect card testing rather than genuine lost cards |
| 43 | Stolen card | Business | As above, and the same caution applies |
| 14 | Invalid card number | Business | Data entry or a bad stored credential. Clustered at one merchant means a checkout or integration problem |
| 91 | Issuer or switch inoperative | Technical | The issuer host is unreachable. Check whether it clusters in a time window: a spike bounded in time is an outage, a steady level is a capacity or configuration problem |
| 96 | System malfunction | Technical | A system in the chain failed mid-processing. Same time-window check as `91` |
| 92 | Financial institution not found | Technical | The request could not be routed to an issuer. This points at BIN routing configuration rather than at the issuer, and an elevated share usually means a routing table is wrong for a card range — it affects whole BIN ranges, not individual cardholders |
| 68 | Response received too late | Technical | The issuer answered after the switch gave up. Two consequences: latency somewhere in the chain, and a reconciliation risk, because the issuer may have authorized a transaction that this data records as declined |

## Reading the classes
- **Business codes** (`05`, `51`, `54`, `61`, `57`, `41`, `43`, `14`) are issuer decisions about the account. They move with customer behaviour, credit limits, card lifecycle and risk policy. Retrying the same transaction immediately does not help, `51` being the partial exception since a balance can change.
- **Technical codes** (`91`, `96`, `92`, `68`) are system failures rather than credit decisions. They are the payment organization's to fix, are usually recoverable, and a spike should be treated as an operational incident, not a customer-behaviour trend.

## Using this in an explanation
When a decline figure looks high, the useful next step is the code mix, not the headline rate: `SHOW volume BY response WHERE status != 'Success'` for the period and group in question, and the same for a comparison. A profile led by `51` is an affordability story; one led by `91`, `96`, `92` or `68` is an availability story; one led by `05` is a story the issuer has to finish telling. Never leave a two-digit code unexplained in an answer.
