---
doc_id: kb-10
title: Analytical Caveats and Common Misreadings
scope: guardrails
topic: interpretation safety
confidence: high (analytical method)
source: project analytical conventions
last_updated: 2026-09-23
---

# Caveats to apply before stating a conclusion

These are the failure modes that turn a correct query result into a wrong answer. Each should be checked before an explanation is offered.

## Counts are not rates
The largest issuer will have the most declines in absolute terms. A count ranking of declines is close to a ranking of size. Any claim that an entity "has a decline problem" must rest on a rate, with a peer comparison.

## Small denominators produce extreme rates
The finer the grouping — issuer by merchant by acquirer by month — the fewer transactions per group. A group with twenty attempts and three declines shows a 15% decline rate that means almost nothing. Before calling a rate high or low, check the underlying volume; flag any conclusion drawn from a small base as provisional.

## Mix effects
An overall rate can move without any individual rate moving, purely because the composition shifted. If a high-decline merchant grew its share of traffic, the portfolio decline rate rises even though nothing got worse anywhere. Before attributing a change to deterioration, check whether the mix changed.

## Value rates and volume rates diverge
A 5% decline rate by count and a 20% decline rate by value describe the same data and imply very different severity. State which measure is being used. When a question is about money at risk, the value-based rate is the honest one.

## "Do not honor" limits the diagnosis
A high share of generic issuer refusals means the reason was not disclosed. The correct response is to say the issuer did not specify, and note what it could plausibly cover — not to pick one cause and assert it.

## Retries inflate declines
A customer who fails twice and succeeds on the third attempt contributes two declines and one success. Decline volumes overstate affected customers, and success rates understate the share of purchases that eventually completed. This matters most for soft declines, which are the ones likely to be retried.

## Technical and business declines are not comparable problems
Combining them into one decline rate averages an outage with a set of credit decisions. Almost every decline question is better answered after the split.

## Correlation is not attribution
An issuer-acquirer path with poor performance may reflect the acquirer's integration, the issuer's infrastructure, the merchant's transaction profile, or the customer segment involved. Name the candidates; do not assert one without evidence that separates them.

## Time windows
"This month" partway through a month is a partial period and cannot be compared like-for-like with a complete prior month. Month-to-date against full-month comparisons are a frequent source of false alarms.

## Ground every claim in the returned data
If a question requires information the query did not return — a prior period, a peer benchmark, a geography, a customer count — the answer is that another query is needed, not an estimate. Never supply a figure that did not come from the result set.

## Denominator size is the first thing to check
The most common misreading in this system is a rate computed on a handful of attempts.

Before ranking or comparing, run the matching volume query — `SHOW volume BY <same dimensions> PERIOD <same period>` — and look at the smallest group. The finer the grouping, the faster the denominators collapse: an issuer over a year is a large number, the same issuer for one merchant in one month is usually a few attempts, and a rate built on a few attempts moves by whole percentage points on a single transaction.

Practical rules:
- Below a few dozen attempts, quote counts rather than rates and say the group is too small to rank.
- When two groups' rates differ by a point or two, check whether each one's month-to-month range overlaps the other before calling it a difference.
- Never rank groups whose denominators differ by an order of magnitude without saying so.

## The zero trap
A group can show zero successful value for two very different reasons: it attempted nothing, or everything it attempted was declined. The displayed 0 looks identical. Check attempted volume before describing a zero, and phrase it as what happened — "one attempt, declined" — rather than "spent nothing", which implies a choice the data does not evidence.

## Mix effects
A group's rate can differ from another's without anything being different about the group itself, because its mix differs:
- **Category mix** — decline rates vary by merchant category, so an issuer whose cardholders shop in higher-decline categories inherits a higher rate.
- **Card-type mix** — Credit, Debit and Prepaid behave differently, and card type is available to split by.
- **Ticket-size mix** — if declines skew small, a count-based rate overstates the money at stake; if they skew large, it understates it.

Each of these is testable: split by the suspected dimension and see whether the gap survives. If it disappears, the mix was the explanation, and saying so is a better answer than naming the group.

## Retries remain invisible
Nothing links an attempt to the retry that follows it, so a cardholder who failed twice before succeeding appears as three rows. Decline counts are **attempts, never people**, and a soft-decline-heavy profile may partly be the same purchase counted several times. Never convert a decline count into a number of affected customers.
