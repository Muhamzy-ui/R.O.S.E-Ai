# SwiftSats

## Problem
- What exact pain point does SwiftSats solve, and how costly is it to the user today?
- *(Fill in: specific friction, time lost, or money lost without SwiftSats)*

## Customers
- **Primary ICP (Ideal Customer Profile):** TBD
- **Acquisition Channel (how they find us today):** TBD
- **Current Waitlist / Signups / Active Users:** TBD

## Revenue Model
- **Pricing Structure (fees, subscriptions, margins):** TBD
- **Payment Rail (e.g., Paystack / transfer / settlement flow):** TBD
- **Unit Economics (Gross margin per tx, CAC, LTV):** TBD

## Status
- **Stage:** TBD (Prototype / MVP / Live Beta / Generating Revenue)
- **Tech Stack & Hosting:** Django + PostgreSQL (read-only R.O.S.E metrics spec ready in `integrations/swiftsats/BACKEND_SPEC.md`)
- **Blocking Technical or Regulatory Work:** TBD

## Risks
- Payment settlement / webhook edge cases (stuck or unverified transactions)
- Authentication abuse / brute-force failed login spikes
- Distribution bottleneck if engineering outpaces customer onboarding
- *(Add venture-specific operational & regulatory risks here)*

## Numbers
- **Daily / Weekly Signups:** TBD
- **Monthly Active Users (MAU):** TBD
- **Monthly Transaction Volume (NGN / USD):** TBD
- **Gross & Net Revenue (Last 30 Days):** TBD
- **Churn / Retention (Week 1 / Month 1):** TBD

## Next Steps
1. Wire read-only `/api/internal/rose-metrics/` endpoint and switch `SWIFTSATS_MODE=live` in `.env`.
2. Verify failed/stuck payment recovery and failed-login alerting thresholds.
3. *(Add top 3 measurable milestones for the next 14 days)*

