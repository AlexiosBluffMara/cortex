# LLC Admin Playbook — Alexios Bluff Mara LLC (dba Red Team Kitchen)

**Owner:** Soumit Lahiri
**Entity:** Alexios Bluff Mara LLC (single-member, IL-domiciled, formed 2025)
**Bank:** Mercury (primary checking + new business debit card)
**Filings/RA:** Bizee
**Tax/bookkeeping (current):** Vyde — under review for replacement
**Generated:** 2026-05-01

> Scope: a one-document operations binder covering doc retrieval, accountant shortlist, Mercury perks, subscription migration order, business-credit timeline, risk hygiene, and a realistic monthly accounting budget. Cap: 600 lines. Sources cited inline; phone numbers footnoted.

---

## 1. The 5-step priority list (do these in order, this week)

1. **Pull every Bizee + Vyde document into `D:\cortex\corp\` today.** You cannot evaluate a tax replacement until you have your books. See sections 2 and 3.
2. **Apply for the free D-U-N-S number this week** (~5 business days standard, 8 days expedited paid). Without a DUNS, no D&B PAYDEX score will ever exist and net-30 vendors won't report.[^dnb] [Source](https://www.dnb.com/en-us/smb/duns/get-a-duns.html)
3. **Move the 3 highest-monthly-cost recurring charges to the new Mercury debit card today** (Claude Max, GitHub Copilot Pro+, Bizee RA). This starts the payment-history clock for D&B and Experian Business immediately. Section 7 has the full migration order.
4. **Open Mercury's free perks dashboard and claim the AWS, Notion, and Stripe credits before quarter-end.** These are time-bounded by account age. Section 6.
5. **Schedule a free 30-min consult with one Chicago CPA** from Section 4 *and* a free intake with the Chicago Lawyers' Committee for Civil Rights [Source](https://www.clccrul.org/nonprofits-small-businesses) — Soumit qualifies (revenue under $500K). Goal: replace Vyde by end of Q2 2026 if a better fit is found.

Total time investment for week 1: ~3 hours. Everything below is execution detail.

---

## 2. Vyde document retrieval (do today)

Vyde is portal-based, not API-based. All deliverables are PDFs in the **My Files** section of the dashboard. There is no public API or SFTP drop. [Source](https://vyde.io/blog/how-to-take-advantage-of-your-mazuma-portal/)

### Step-by-step

1. Navigate to **https://vyde.io** → top-right **Login** button.
2. Email + password (the email Vyde used to onboard you — likely `soumitlahiri@philanthropytraders.com`). If you forgot, click **Forgot Password** and reset to that mailbox.
3. From the dashboard left-rail, click **My Files**. This is the master document store.
4. Use the year-folder dropdown (2024, 2025) to filter. Download every PDF in each folder. Expected files for a 2025-formed LLC:
   - **EIN Confirmation Letter (CP-575)** — IRS letter; you may also have it from Bizee. Keep both copies.
   - **Form 1065** (if Vyde treated the LLC as a multi-member partnership — confirm) or **Schedule C attachment** to your 1040 if single-member disregarded.
   - **K-1s** — only if multi-member; single-member disregarded LLCs do not generate K-1s. Skip if N/A.
   - **1099s issued (1099-NEC / 1099-MISC)** — any contractors you paid >$600.
   - **1099s received** — Mercury issues 1099-INT if you earned interest; Stripe issues 1099-K if you took card payments.
   - **Bookkeeping ledger / P&L / Balance Sheet** — usually under a sub-folder named *Financials* or *Year-End Reports*.
   - **Quarterly estimated tax vouchers (1040-ES)** for 2026 if Vyde set them up.
5. Click **Messages** (or the chat-bubble icon). Export the full thread with your assigned accountant — right-click → *Save As* the page, or screenshot every page. This is your audit trail of advice given.
6. Save everything to `D:\cortex\corp\vyde\YYYY\` mirroring Vyde's folder structure.

### Cancellation / transfer

- Vyde is **month-to-month, no contract** [Source](https://vyde.io/pricing/). Cancel by emailing your assigned accountant (find the address in the **Messages** thread) AND by submitting the support form at vyde.io/contact. Get email confirmation in writing.
- Best timing: cancel **after** they file your 2025 taxes (Mar–Apr 2026 timeframe; if they've already filed, you're clear). Don't cancel mid-prep — you'll pay another firm to redo work.
- Vyde will retain copies of your prior-year work papers for legal-required retention but **will not** continue to support you, so download everything before cancelling.

---

## 3. Bizee document retrieval (do today)

Bizee delivers documents via the post-2023 dashboard (UI was redesigned during the Incfile→Bizee rebrand). All docs are PDFs available indefinitely while your registered-agent service is active. [Source](https://bizee.com/manage-your-company)

### Step-by-step

1. Navigate to **https://bizee.com** → top-right **Login**.
2. From the dashboard, look for the left-rail **Documents** or **Company Documents** section (location varies; in the redesigned UI it's a card on the main dashboard labeled *Your Documents*).
3. Download the following — every LLC formation package includes:
   - **Articles of Organization** (Illinois Form LLC-5.5, stamped/filed by the IL Secretary of State)
   - **EIN Confirmation Letter (CP-575)** from the IRS
   - **Operating Agreement template** (Bizee provides a fill-in-the-blank — if you never customized it, you have a *blank* template, which is risky for veil-piercing — see Section 9)
   - **Statement of Organizer**
   - **IL SOS receipt / cover letter**
4. Click **Registered Agent** in the dashboard. Note the registered-agent address on file (Bizee's IL agent address) and your **next renewal date**. Bizee charges **$119/year** after the first free year. [Source](https://bizee.com/business-management/registered-agent-change)
5. Click **Compliance** or **Annual Report**. Illinois LLCs must file an annual report with the SOS — fee is $75. Bizee will remind you; don't miss it.[^ilreport]
6. Save everything to `D:\cortex\corp\bizee\formation\`.

### Cancellation / transfer (only if you want to leave)

- The 24-hour Bizee cancellation window is for refund-on-formation only; it has long passed. You cannot get a formation refund. [Source](https://www.zenbusiness.com/incfile-registered-agent-service-review/)
- To **switch registered agents**, you don't cancel Bizee — you file a *Change of Registered Agent* (IL Form LLC-1.36/1.37, $25 fee) with the IL SOS naming the new agent. Bizee will then stop billing once the state record updates. [Source](https://bizee.com/business-management/registered-agent-change)
- **Recommendation: keep Bizee.** $119/yr is competitive (Northwest is $125, ZenBusiness $199). [Source](https://venturesmarter.com/northwest-registered-agent-vs-bizee/) Switching has no real upside unless you want privacy upgrades Bizee already provides.

---

## 4. Chicago accountant shortlist

All four below were filtered for: (a) Chicago-area, (b) explicitly serving small business / startups / LLCs, (c) using modern integrations (QuickBooks Online, Xero), (d) flat-fee or tiered pricing under ~$500/mo for bookkeeping. Verify minority-owned status during your intro call — it's not consistently disclosed online.

| Firm | Pricing | Includes | Phone | Website |
|---|---|---|---|---|
| **SDO CPA** | Bookkeeping **$300–500/mo** single entity; full-service (books + tax + advisory) **$1,000–2,000/mo** with upfront estimate | S-Corp / partnership specialty, virtual delivery, fractional CFO option | website intake | [sdocpa.com](https://www.sdocpa.com/chicago-cpa-firm/) |
| **Pasquesi Partners** | Early-stage range **$500–2,000/mo** for books + reporting + tax planning | Founded 2014, startup focus, multi-state | website intake | [pasquesipartners.com](https://pasquesipartners.com/industries/startups/) |
| **Lewis CPA** | Custom (request quote) | Bookkeeping, payroll, tax. Strong on IL-specific tax guidance — they publish the *Illinois LLC Taxes* guide. | (630) 552-6531[^lewis] | [lewis.cpa](https://www.lewis.cpa/bookkeeping) |
| **Massey and Company CPA** | Custom; small-business focus | Tax + advisory + bookkeeping for service businesses | website intake | [masseyandcompanycpa.com](https://masseyandcompanycpa.com/chicago/) |
| **OPS Accounting** | Custom; volume-priced | One-stop for QuickBooks bookkeeping + payroll + income tax; Chicago + Vernon Hills offices | website intake | [opsaccounting.com](https://opsaccounting.com/) |

**How to evaluate in a 30-min consult:** ask (1) Do you sync Mercury directly via the QBO/Xero feed? (2) What's your monthly minimum? (3) Are you a fixed-fee or hourly shop? (4) Will I have a *named* accountant or a pool? (5) Do you handle IL annual report + sales tax filings? (6) What's your cost to do *just* the year-end return if I do my own books in QBO?

**Top pick to call first: SDO CPA.** Their bookkeeping starts at the lowest documented price point ($300/mo) for a single entity, and they explicitly serve S-Corps — useful if you elect S-Corp taxation later for self-employment-tax savings.

---

## 5. Online accountant alternative (Mercury-friendly)

If you'd rather stay fully remote and software-driven, three options are credible and Mercury-compatible. **All three rely on QuickBooks Online or Xero as the ledger** — Mercury's accounting integrations sync to QBO, Xero, and NetSuite for free. [Source](https://mercury.com/accounting-automations) Bench shut down in 2024 [Source](https://getholdings.com/resources/blog/bench-alternatives-2026); do not use it.

| Service | Pricing | Mercury integration quality | Notes |
|---|---|---|---|
| **Pilot** | From **$499/mo** | Indirect — Pilot uses QBO/NetSuite as the ledger and pulls Mercury via the standard QBO bank feed. [Source](https://pilot.com/integrations/mercury-bank) | Tech-startup focus, accrual books supported. Premium pricing. |
| **1-800Accountant** | Bookkeeping starts **$159/mo**; bundle with tax filing **~$399–419/mo** | QBO-via-bank-feed; not Mercury-native | Cheapest credible option; less white-glove. [Source](https://bookkeeping-services.com/1-800accountant-review/) |
| **Bookkeeper360** | Starter **$399/mo**; add-ons **+$125–200** for payroll / sales tax / AP-AR | QBO + Xero. | Premium upsell-heavy; thoroughly reviewed. [Source](https://bookkeeping-services.com/bookkeeper360-review/) |

**Software-only path (cheapest functional setup):** QuickBooks Online Simple Start (~$30/mo) + Mercury's free QBO sync + a Chicago CPA (Section 4) doing only the year-end return for $800–1,500. This is what I'd recommend at Soumit's volume (single-member LLC, sub-six-figures revenue).

---

## 6. Mercury benefits table (free unless noted)

Mercury's value for a solo LLC is heavy on *included* features that competitors charge for. Source for unmarked rows: [mercury.com/perks](https://mercury.com/perks), [mercury.com/pricing](https://mercury.com/pricing), [mercury.com/accounting-automations](https://mercury.com/accounting-automations).

| Benefit | Cost | What you get |
|---|---|---|
| Mercury Checking + Savings | Free | No min balance, no monthly fee, FDIC via partner banks |
| Mercury Debit Card (just received) | Free | Standard interchange; this is the one you'll use for subscriptions |
| Mercury IO credit card | Free, no PG, 1.5% cashback | Charge card; auto-pays in full each statement; 0% APR effectively. [Source](https://corporatecreditcardforstartups.com/amex-vs-brex-vs-mercury/) Eligibility usually requires steady deposits — check IO tab in dashboard. |
| Mercury Treasury (sweep) | Free | Earn yield on idle balance via money-market funds (subject to balance threshold) |
| QBO / Xero / NetSuite sync | Free | Auto-categorization, AI transaction coding |
| Bill Pay (basic) | Free | ACH and check |
| Paid Plan ($35/mo) | $35/mo | Advanced workflows, expense reimbursement >5 users, enriched NetSuite. **Skip for now** — solo, no team. |
| **Mercury Raise** | Free | Founder Slack community, expert AMAs, Investor Connect (pitch-to-VCs feed). No equity, no investment from Mercury. [Source](https://mercury.com/raise/fundraising-support) |
| **Mercury Perks (300+)** | Free claims for active customers | Curated startup-launch bundle. Highlights below. |

### Headline perks worth claiming (Section 4 of the priority list)

| Perk | Value | Source |
|---|---|---|
| **AWS** | **$5,000** in credits + Activate program enrollment | [mercury.com/perks/aws](https://mercury.com/perks/aws) |
| **Microsoft for Startups** | $5,000 in Azure credits + Microsoft 365 + GitHub | [mercury.com/perks/microsoft-for-startups](https://mercury.com/perks/microsoft-for-startups) |
| **ClickUp** | $3,000 credit | [mercury.com/perks/click-up](https://mercury.com/perks/click-up) |
| **Notion** | Discounted Plus/Business plans | [mercurydocumentation.com](https://mercurydocumentation.com/startup-banking-discounts-aws-notion-perks) |
| **HubSpot** | Reduced CRM pricing | same source |
| **Stripe** | Fee credits / waived processing on first $X | [mercury.com/perks](https://mercury.com/perks) |
| **Carta** | Cap-table free / discounted (verify in dashboard) | [carta.com/partners/startup-stack](https://carta.com/partners/startup-stack/) |
| **Account-opening cashback** | $150 promo (verify still active 2026) | [elevate.store/tools/mercury-bank](https://elevate.store/tools/mercury-bank) |

> **Biggest hidden lever: AWS $5K + Microsoft $5K + ClickUp $3K = $13K of credits you can claim today and burn before quarter-end.** Even if you don't use AWS for Cortex (you're local-first), $5K of S3/Lambda costs nothing to spin up and looks like real spend on D&B-reporting business activity.

---

## 7. Subscription migration order (credit-velocity optimized)

D&B PAYDEX needs **3 reporting tradelines minimum** to issue a score. Most SaaS *don't* report; only net-30 vendors do. So the migration has two parallel tracks: (A) move all card subscriptions to the LLC debit immediately for *Experian Business* and bank-history purposes, (B) open net-30 vendor accounts (Section 8) for *D&B PAYDEX*.

### Migration order — do in this sequence today

| # | Subscription | Monthly | Action | Why this order |
|---|---|---|---|---|
| 1 | **Claude Max** | $100 | Update card to Mercury debit | Largest recurring spend; high-confidence renewal |
| 2 | **GitHub Copilot Pro+** | ~$39 | Update card | Auto-renew, predictable |
| 3 | **Bizee Registered Agent** | $119/yr ($9.92/mo amortized) | Update card | Already a *business* expense; cleanest paper trail |
| 4 | **ChatGPT Plus / Pro** (if active) | $20–200 | Update card | Solo dev SaaS |
| 5 | **Cursor** (if paid tier) | $20 | Update card | Same |
| 6 | **GitHub paid plan** (if Team/Pro) | $4–21 | Update card | Same |
| 7 | **Cloudflare** (currently free) | $0 | When you upgrade, use LLC card | Pre-empt: any future paid tier (Workers Paid, R2, Pages) goes here |
| 8 | **Tailscale** (currently free) | $0 | Same | Same |
| 9 | **Domain renewals** (redteamkitchen.com via Cloudflare) | ~$10/yr | Update card | One-time annual but reportable |
| 10 | **Vyde** | $? | DO NOT migrate yet — let it lapse on personal card if cancelling | Don't add a new charge to a service you're leaving |
| 11 | **ElevenLabs / HuggingFace Pro / Notion Pro / Firecrawl** (any active) | varies | Update card | Long tail |

**Reimbursement / discount opportunities (scan before migrating):**

- **GitHub Copilot Pro+** → free if you still have any verified-student status; check via the GitHub Student Pack page. (Soumit is Purdue alumni — likely no longer eligible. Confirm before paying.)
- **Notion** → discounted via Mercury Perks (Section 6).
- **AWS** → don't pay; use the $5K credit.
- **Microsoft 365** → free via Microsoft for Startups perk if you're on it.
- **Anthropic / OpenAI** → no minority discount currently published; pay via LLC card.
- **Cloudflare** → no nonprofit discount unless 501(c)(3); skip.

**Why this order builds credit fastest:** D&B's PAYDEX algorithm weights *number of tradelines* and *months of on-time payment*. Mercury debit charges don't report directly to D&B but they *do* flow into your bank-statement record, which is what every business-credit underwriter looks at when you eventually apply for a real business credit card (Section 8). The single most-impactful move: **migrate Claude Max today** — it's the largest recurring charge and creates a 12-month consistent-payment trail by May 2027.

---

## 8. DUNS + business credit timeline

Source for the timeline below: [Nav PAYDEX guide](https://www.nav.com/business-credit-scores/dun-bradstreet-paydex/) and [unitedcapitalsource DUNS guide](https://www.unitedcapitalsource.com/blog/dnb-duns-number/).

### Week-by-week plan (today = Week 0)

| Week | Action |
|---|---|
| 0 (today) | Apply for free DUNS at [dnb.com/en-us/smb/duns/get-a-duns.html](https://www.dnb.com/en-us/smb/duns/get-a-duns.html). Use legal name *Alexios Bluff Mara LLC*, EIN, IL principal address. **Standard 30 business days, expedited 8 business days for a fee.** |
| 0–1 | Migrate the Section 7 subscription list to Mercury debit. |
| 1 | Open **2 net-30 vendor accounts** that report to D&B: **Uline** (industrial supplies — buy something cheap and useful), **Quill** (office supplies). Pay invoice within 10 days for max PAYDEX boost. [Source](https://ramp.com/blog/best-net-30-accounts) |
| 2 | DUNS arrives (if expedited). Add it to your records. |
| 4 | Open **Crown Office Supplies** as 3rd net-30 (lower bar, approves new LLCs). |
| 8–12 | Vendors report your first invoices. PAYDEX score appears once **3 trade experiences** are on file. |
| 12 | Check D&B free profile (CreditSignal). You should see a PAYDEX between 70–80 if you paid early. |
| 16 | Open **Grainger** net-30 (stricter — needs prior tradelines). |
| 20–26 | Apply for **Brex** or **Ramp** business credit card (no PG, no personal credit pull). |

### Card eligibility reality check

- **Brex** requires **$50,000 cash reserves** in a US business bank account. [Source](https://ramp.com/blog/brex-business-credit-card-requirements)
- **Ramp** requires **$25,000 cash**. [Source](https://www.nav.com/blog/brex-business-card/)
- **Mercury IO** typically gates on consistent monthly deposits — easier path if Soumit's LLC has steady inflows even at small dollar amounts.

If LLC cash is below $25K, stick with the **Mercury debit card** until Cortex/RTK revenue is steady, then upgrade. Don't take a personal-guarantee SBA card — that defeats the LLC liability shield.

---

## 9. Risks: things to watch (single-member LLC hygiene)

The single biggest legal risk for a solo LLC is **veil-piercing** — a court treating the LLC as a sham and exposing personal assets. Illinois law specifically holds that LLC veil-piercing **cannot** be based on failure-to-follow-formalities (unlike corporations) [Source](https://www.isba.org/sites/default/files/cle/Piercing%20Veils,%20Changing%20Members,%20Charging%20Orders%20and%20Blood%20from%20Turnips%20-%20HANDOUT.pdf), but commingling and undercapitalization still pierce.

### Hygiene rules — non-negotiable

1. **Never run a personal charge through the LLC card. Never run a business charge through your personal card.** This is the single most-common veil-piercing predicate.
2. **Have a signed, executed Operating Agreement** even though you're single-member and IL doesn't require it. Bizee gave you a *blank template* — fill it in, sign it, date it, store the PDF in `D:\cortex\corp\`. Free template scrub by Chicago Lawyers' Committee for Civil Rights [Source](https://www.clccrul.org/nonprofits-small-businesses) is the cheapest path.
3. **Capitalize the LLC.** Don't run it on $0 — keep at least one month of operating expenses in the Mercury checking. An undercapitalized LLC is a veil-pierce risk.
4. **Document money movements.** Owner draws → memo line *"Owner Distribution"*. Owner contributions → memo *"Capital Contribution"*. Don't transfer with no memo.
5. **Illinois sales tax: register if you sell taxable goods/services.** Register at [mytax.illinois.gov](https://mytax.illinois.gov). [Source](https://tax.illinois.gov/businesses/registration.html) For pure SaaS / consulting / B2B services, you likely have no sales-tax obligation, but Cortex (if monetized as a downloadable product) might. Confirm with the CPA you hire.
6. **File the Illinois annual report on time.** $75/yr to the SOS. Bizee reminds you; missing it dissolves the LLC after 6 months.[^ilreport]
7. **EIN ≠ DUNS ≠ ITIN.** Don't conflate. EIN from IRS, DUNS from D&B, ITIN is for individuals without SSNs (not relevant).
8. **1099-NEC obligation:** if you pay any contractor >$600 in 2026, issue a 1099-NEC by Jan 31 2027. Mercury has a contractor-payment workflow that auto-collects W-9s — use it.

### Free legal: Chicago Lawyers' Committee for Civil Rights

- Free legal services to small businesses with revenue under $500K — Soumit qualifies.
- Help with: **contract drafting/review, operating agreement scrub, lease review, employment matters, IP basics**.
- Apply at [clccrul.org/nonprofits-small-businesses](https://www.clccrul.org/nonprofits-small-businesses) — submit intake form, staff schedules a consult.
- Backup option: **Law Office of David Hyde**, free 30-min consult, women-owned-business focus but takes general LLC matters: (312) 210-9598[^hyde]. [Source](https://davidhydelaw.com/small-business-lawyer-chicago-il-women-owned-small-business/)

---

## 10. Budget: realistic monthly accounting cost

Three tiers, pick one. All assume Soumit does his own day-to-day expense capture (snap receipts in Mercury app).

| Tier | Stack | Monthly | Year-end | Total/yr |
|---|---|---|---|---|
| **Lean** (recommended now) | Mercury free + QBO Simple Start ($30) + DIY books + Chicago CPA only at year-end | **$30/mo** | ~$1,200 (one-time return) | **~$1,560** |
| **Hybrid** | Mercury + QBO Essentials ($60) + SDO CPA bookkeeping ($300) | **$360/mo** | included | **~$4,320** |
| **Full-service** | Mercury + Pilot ($499) | **$499/mo** | included | **~$5,988** |

**Recommendation: Lean tier through 2026.** At pre-revenue / single-member volume, $300/mo for outside bookkeeping is overkill. Get QBO syncing Mercury, snap receipts, hand the file to a CPA in March. Re-evaluate once Cortex or any other Red Team Kitchen revenue line crosses ~$5K MRR — at that point the time savings of outsourced books pay for themselves.

**Net replacement plan vs. Vyde:** unless Vyde is already pricing under $200/mo *and* doing both books and tax, the Lean tier with SDO CPA at year-end will cost less and give you direct QBO data ownership (vs. trapped in Vyde's portal).

---

## Appendix A: Document folder structure

Create on the local Windows drive *and* mirror to a Cloudflare R2 backup bucket (use the cloudflare skill).

```
D:\cortex\corp\
  formation\
    articles_of_organization.pdf
    operating_agreement_signed.pdf
    ein_cp575.pdf
    il_sos_filing_receipt.pdf
  bizee\
    dashboard_export_2026-05-01\
    registered_agent_renewals\
  vyde\
    2024\
    2025\
    messages_export.pdf
  taxes\
    2025\
      form_1065_or_sched_c.pdf
      1099s_issued\
      1099s_received\
      estimated_payments\
  banking\
    mercury_statements\
      YYYY-MM.pdf
    debit_card_records.md
  vendors\
    duns_certificate.pdf
    net30_invoices\
      uline\
      quill\
  legal\
    clcc_consult_notes.md
    contracts_signed\
  insurance\           # placeholder — get a $400/yr GL policy from Hiscox once revenue starts
  meetings\
    2026_member_resolutions.md
```

## Appendix B: Things explicitly *not* recommended

- **Bench Accounting** — shut down in 2024. [Source](https://getholdings.com/resources/blog/bench-alternatives-2026)
- **SBA personal-guarantee credit cards** — defeats the LLC liability shield.
- **Switching off Bizee for cosmetic reasons** — $119/yr is competitive and the switch costs $25 in IL filing fees.
- **Mercury IO before steady deposits** — wait until you have 3 months of consistent deposits visible in Mercury history.
- **Paying Vyde and a new CPA simultaneously** — finalize the cancellation timing before signing new bookkeeping engagement.

## Appendix C: Today vs. this quarter checklist

**Today (≤ 1 hour):**
- [ ] Log into Vyde, download all PDFs to `D:\cortex\corp\vyde\`
- [ ] Log into Bizee, download formation packet to `D:\cortex\corp\bizee\`
- [ ] Apply for free DUNS at dnb.com
- [ ] Update Claude Max + GitHub Copilot Pro+ payment method to LLC debit card

**This week:**
- [ ] Migrate remaining Section 7 subscriptions to LLC card
- [ ] Claim AWS $5K + Microsoft $5K perks via mercury.com/perks
- [ ] Open Uline + Quill net-30 accounts
- [ ] Submit intake form at clccrul.org for free legal consult
- [ ] Schedule call with SDO CPA

**This quarter (by Jul 31, 2026):**
- [ ] Sign and date Operating Agreement (with CLCC review if possible)
- [ ] Decide: stay with Vyde or migrate to Lean tier (QBO + year-end CPA)
- [ ] Open 3rd net-30 (Crown Office Supplies)
- [ ] File IL annual report if anniversary falls in this window
- [ ] Confirm 2026 estimated tax payments scheduled (Q2 due Jun 17, Q3 Sep 16)
- [ ] Verify D&B PAYDEX score has populated (~12 weeks after first net-30 invoices report)

---

[^dnb]: D&B free DUNS application: 30 business days standard, 8 business days expedited (paid). Apply at [dnb.com/en-us/smb/duns/get-a-duns.html](https://www.dnb.com/en-us/smb/duns/get-a-duns.html).
[^ilreport]: Illinois LLC annual report: $75 fee, due first day of anniversary month. File at [ilsos.gov](https://www.ilsos.gov/). Late fee + administrative dissolution after 6 months.
[^lewis]: Lewis CPA verified phone: (630) 552-6531 (per lewis.cpa contact page).
[^hyde]: Law Office of David Hyde verified phone: (312) 210-9598 (per davidhydelaw.com).

---

*End of playbook. Update quarterly or whenever a major service changes.*
