---
doc_id: kb-07
title: Interpretation Playbook — Why Are Declines High?
scope: reasoning playbook
topic: decline analysis
confidence: high (analytical method)
source: project analytical conventions
last_updated: 2026-09-23
---

# Playbook: explaining a high decline rate

Applies to questions of the form "why does this issuer have such high declines", "what's driving declines at this merchant", "why did our approval rate drop".

## The mistake to avoid
A high decline count is not by itself a finding. Before explaining *why*, establish *whether* the rate is genuinely high — a large issuer will have the most declines in absolute terms simply because it has the most transactions. **Always work from rates, not counts, when the question is about a problem; use counts only to size the impact once a rate difference is confirmed.**

## Reasoning sequence
1. **Establish the rate.** Declined volume over attempted volume for the entity in question, and the same figure for comparable entities. If the rate is in line with peers, the honest answer is that the entity is not an outlier — it is simply large.
2. **Split by `TD_BD`.** Is this a technical problem (systems, outage, routing) or a business problem (limits, balances, risk policy)? These lead to completely different explanations and owners.
3. **Break down by response code.** The top codes by volume, and separately by value, name the actual mechanism. Two or three codes usually account for most of the gap.
4. **Translate each code into plain business language** using the response code reference and decline taxonomy — do not leave a two-digit code unexplained in an answer.
5. **Check whether it is isolated.** Is the pattern specific to one acquirer, one merchant, or one time window? A problem confined to a single issuer-acquirer path is a routing or integration issue; one that spans all paths for an issuer is an issuer-side issue.
6. **Size the impact in value.** Declined value is the money at stake. Report it alongside the rate.

## Reading common decline profiles
**Dominated by 90-series codes (91, 92, 96)** — infrastructure. An issuer host down or timing out, a switch failure, or a routing misconfiguration. Usually time-bounded; check whether it clusters in a window. Recoverable and actionable by the payments organization.

**Dominated by insufficient funds (51) and limit codes (61, 65)** — customer balances and issuer limits. Correlates with high-ticket transactions, salary cycles, and month-end. Largely outside the organization's control; the lever is retry timing and ticket-size handling.

**Dominated by do not honor (05)** — the least informative outcome. The issuer refused without stating a reason, so it can hide balance, risk-scoring, or block reasons. A high `05` share means the diagnosis is limited by what the issuer discloses, and that should be stated in the answer rather than guessed past.

**Dominated by card-status and fraud codes (41, 43, 57, 59, 62)** — risk and card lifecycle. Expect these to be small in share; if they are large for one merchant, suspect card-testing or fraud probing at that merchant.

**Dominated by expired card (54) and invalid number (14)** — data entry or stale credentials, most common in card-not-present and recurring-billing contexts.

## How to phrase the conclusion
State the mechanism, not just the code. "This issuer's elevated decline rate is driven mainly by technical declines — issuer or switch inoperative and system malfunction — which indicates issuer-side availability problems rather than customers being refused" is a finding. "The top code is 91" is not.

## Worked method: "why is <issuer> Bank's decline rate so high this year?"
The sequence above, as the queries that answer it. Every number in the answer comes from a query run at the time of asking; none of it is remembered.

| Step | Query | What the answer tells you |
|---|---|---|
| 1. Is it actually high? | `SHOW decline_rate BY issuer` | The issuer beside its peers. If it sits with them, the honest answer is that it is not an outlier |
| 2. Against the whole book | `SHOW decline_rate` | The portfolio rate, the second comparison worth naming |
| 3. Systems or people? | `SHOW decline_rate, business_decline_rate, technical_decline_rate BY issuer` | Whether the gap is on the business or the technical side. These lead to different owners |
| 4. Which mechanism? | `SHOW volume BY response WHERE issuer = '<name>' AND status != 'Success' ORDER BY volume DESC` | The codes that produce the rate. Two or three usually account for the gap |
| 5. Is it a moment or a pattern? | `SHOW decline_rate BY month WHERE issuer = '<name>'` | Whether one month drives the year, and how wide the issuer's own month-to-month range is |
| 6. Is it isolated? | `SHOW decline_rate BY merchant WHERE issuer = '<name>'` | Whether one path carries the difference, or it is spread across all of them |
| 7. What is it worth? | `SHOW decline_value BY issuer IN LAKH` | The money at stake, which the rate alone never says |

**Reading it back.** State the comparison you used, the class split, the codes, and the size of the difference in transactions rather than only in percentage points — a rate gap on a few hundred attempts can be a handful of transactions. If the profile is led by `05`, say plainly that the issuer has not disclosed a reason and the diagnosis stops there.

**A gap of one or two points between issuers is usually not a finding.** Before calling it one, check step 5: if the month-to-month range of each issuer overlaps, the issuers are indistinguishable and the ranking would change next month.

## Worked method: a single month
"The issuer with the highest decline rate this month" is `SHOW decline_rate BY issuer PERIOD MTD ORDER BY decline_rate DESC LIMIT 1`. Two cautions before reporting the winner:

- **`decline_rate` means all declines.** `business_decline_rate` answers a narrower question and can rank issuers differently, because technical declines sit outside it. Whichever is used, say which.
- **A month is a small denominator.** Run `SHOW volume BY issuer PERIOD MTD` alongside it: if the groups hold only a hundred-odd attempts, a couple of transactions decide the ranking, and the right framing is "indistinguishable this month" rather than a league table.
