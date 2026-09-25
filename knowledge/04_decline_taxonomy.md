---
doc_id: kb-04
title: Decline Taxonomy — Technical vs Business, Soft vs Hard
scope: definitions
topic: decline classification
confidence: MEDIUM — the TD/BD split below is the standard convention; the authoritative mapping for this database lives in response_master
source: general payments industry knowledge; project schema
last_updated: 2026-09-23
---

# Classifying declines

Two independent classifications are applied to the same response code. They answer different questions and should not be mixed.

## 1. Technical decline vs business decline (the `TD_BD` axis)
This is the primary split used in this database, carried on the `response_master` table and exposed as three outcome states.

**Success** — the transaction was approved. Response code `00`.

**Business decline (BD)** — the issuer deliberately refused, based on the account, the card, or its risk policy. The system worked correctly; the answer was no. Typical codes: insufficient funds, exceeds limits, restricted card, lost or stolen card, expired card, do not honor, transaction not permitted.
*Owner:* the issuer's risk and credit policy, or the cardholder's own account state.
*What it signals:* customer behaviour, issuer risk appetite, or limit configuration.

**Technical decline (TD)** — the transaction failed because a system in the chain could not process it, not because anyone decided to refuse. Typical codes: issuer or switch inoperative, system malfunction, routing errors, format errors, duplicate transmission.
*Owner:* the issuer's infrastructure, the network switch, or the acquirer's integration.
*What it signals:* an outage, a capacity problem, or a misconfiguration.

**Why the split matters:** technical declines are almost always actionable by the payments organization and are usually recoverable. Business declines are mostly outside the organization's control and are a demand or risk signal rather than a defect. A merchant whose declines are 80% technical has a very different problem from one whose declines are 80% business, even at the same headline decline rate. Always split the decline rate by `TD_BD` before drawing a conclusion.

## 2. Soft decline vs hard decline (the retry axis)
An orthogonal, retry-oriented classification used mainly in operations rather than reporting.

**Soft decline** — a temporary or recoverable condition that may succeed if retried later or under different conditions. Examples: insufficient funds, issuer unavailable, system malfunction, do not honor.

**Hard decline** — a permanent condition that should never be retried with the same card. Examples: stolen card, expired card, invalid card number, lost card. Repeated attempts on a card flagged for fraud can increase chargeback exposure and trigger processor risk review.

Note the axes cross: `91` (issuer inoperative) is a *technical* decline and a *soft* decline. `54` (expired card) is a *business* decline and a *hard* decline. `51` (insufficient funds) is a *business* decline but a *soft* one.

## Practical rule for interpretation
When explaining a decline profile, name the `TD_BD` class first (is this a systems problem or a customer/policy problem?), then use soft/hard to say whether anything can be recovered by retrying.

## Both axes together
`TD_BD` comes from `response_master` and is authoritative. Soft/hard is a retry judgement that this database does not store — it follows from what the code means.

| Code | Meaning | TD/BD | Soft/hard |
|---|---|---|---|
| 51 | Insufficient funds | Business | Soft — a balance can change |
| 05 | Do not honor | Business | Soft, but blind: the reason is undisclosed |
| 61 | Exceeds withdrawal amount limit | Business | Soft — a smaller amount or a later day may pass |
| 54 | Expired card | Business | Hard — needs new card details |
| 57 | Transaction not permitted to cardholder | Business | Hard — the card is not enabled for this |
| 41 | Lost card | Business | Hard — never retry |
| 43 | Stolen card | Business | Hard — never retry |
| 14 | Invalid card number | Business | Hard — the details are wrong |
| 91 | Issuer or switch inoperative | Technical | Soft — retry once the issuer is back |
| 96 | System malfunction | Technical | Soft — retry after the fault clears |
| 92 | Financial institution not found | Technical | Soft — routing, not the cardholder |
| 68 | Response received too late | Technical | Soft, with care: the issuer may have approved it |

**Recoverable share.** The proportion of declines that could in principle succeed on a later attempt is a genuinely useful answer, and it is computed, not remembered: break the declines down by code (`SHOW volume BY response WHERE status != 'Success'`) and add up the soft ones using the table above. Quote it as a ceiling on recovery, not a forecast — a soft code means retryable, not that a retry will work.

**The business-to-technical ratio** is the other number worth computing before explaining anything: `SHOW volume BY status` for the group, and the same for a comparison group. A group far from the usual ratio is the finding; a group at it is ordinary, whatever its headline rate.
