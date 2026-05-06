# GCP Billing Support Case — Refund + Going-Forward Credits

**Submit at:** https://console.cloud.google.com/support → Create Case → Category: **Billing**
**Severity:** S3 (or S2 if available — billing matters)
**Subject:** First-time spike refund + research/startup credit application — ABM-ISU project

---

## ⚠️ Before submitting, fill in the placeholders:

- `[BILL_TOTAL]` — total $ from https://console.cloud.google.com/billing/01F58E-689697-ADE7FC/reports
- `[INVOICE_DATES]` — from https://console.cloud.google.com/billing/01F58E-689697-ADE7FC/payment
- `[ISU_PROFESSOR_NAMES]` — names + departments of the ISU faculty you're working with
- `[ISU_PROJECT_TITLE]` — the formal title of the research project
- `[AWS_CERT]` — exact AWS certification(s) you hold (e.g. "AWS Certified Solutions Architect — Associate")
- `[NOUS_RESEARCH_LINK]` — link to your Nous Research Twitter submission once posted

---

## Case body (paste the section below into the support form)

```
Subject: First-time spike refund + research/startup credit application — ABM-ISU project

Hello Google Cloud Billing Support,

I'm Soumit Lahiri, founder of Alexios Bluff Mara LLC (dba Red Team Kitchen),
a minority-owned business based in Chicago, Illinois. I'm writing to request
two things in one case:

1. A refund or credit for unintended spend during the first 5 days of my
   project's existence, totaling approximately $[BILL_TOTAL] across
   invoice(s) [INVOICE_DATES].

2. Enrollment in the appropriate ongoing credit programs to continue this
   work, given the academic and minority-business angles described below.

═══════════════════════════════════════════════════════════════════
PART 1 — WHO I AM AND WHY I'M ON GOOGLE CLOUD
═══════════════════════════════════════════════════════════════════

• I'm a Chicago-based founder of a minority-owned LLC (Alexios Bluff Mara LLC,
  dba Red Team Kitchen).
• My existing technical experience is on AWS — I hold [AWS_CERT].
• I chose to try Google Cloud specifically because of the new Google office
  presence in Chicago (320 N Morgan St., Fulton Market) and the company's
  Mid-American expansion. I attended early Chicago Partner Kickstart
  programming and wanted to build my next project on GCP rather than AWS as
  a result.
• My current project (described below) is being submitted to the Gemma 4
  Good Hackathon hosted by Kaggle + Google DeepMind, deadline May 18, 2026.
  This is a $200K-prize-pool Google-sponsored event focused on AI for
  health/education/social-good, and it is the entire reason this project
  exists today.
• I'm collaborating directly with faculty at Illinois State University
  ([ISU_PROFESSOR_NAMES]) on the academic research framing of this project.
  Documentation, project plan, and faculty correspondence are available
  on request.

═══════════════════════════════════════════════════════════════════
PART 2 — WHAT THE PROJECT IS
═══════════════════════════════════════════════════════════════════

Codename: Cortex (https://github.com/[your-repo] — happy to grant Google
employee access if helpful).

Cortex is an open-source neuroscience tool that takes a video clip as input,
predicts the cortical brain response across 20,484 vertices using the TRIBE
v2 brain foundation model, and produces a multi-tier natural-language
narration of the activation pattern at expertise levels from "toddler" to
"clinician/researcher." The narration generation is what hit Gemini.

Direct relevance to "AI for Good":
- Health & Sciences track of the Gemma 4 hackathon — predicting and
  explaining brain response is a research-tool primitive.
- Digital Equity — the multi-tier narration is designed so non-specialists
  can understand fMRI-style outputs (parents of children with neurological
  conditions, clinicians without an fMRI background, students, etc.).
- Future of Education — direct collaboration with [ISU_PROFESSOR_NAMES] at
  Illinois State University ([ISU_PROJECT_TITLE]) targeting student-facing
  educational tooling around brain/cognition.

Same project also under public review by Nous Research (independent AI
research community) — submission at [NOUS_RESEARCH_LINK].

═══════════════════════════════════════════════════════════════════
PART 3 — THE BILLING INCIDENT
═══════════════════════════════════════════════════════════════════

Project ID: abm-isu (project number 846100819386)
Billing account: 01F58E-689697-ADE7FC
Project creation date: 2026-04-26 13:04 UTC
First chargeable usage: SAME DAY as project creation
Period of unintended spend: 2026-04-26 through 2026-04-30

Cloud Monitoring evidence (serviceruntime.googleapis.com/api/request_count
across all services, last 60 days):

  Service                                                Total requests
  generativelanguage.googleapis.com                              11,035
  cloudbuild.googleapis.com                                         983
  run.googleapis.com                                                494
  All other services combined                                      ~830

97% of all activity on this brand-new project was Gemini API calls —
overwhelmingly the dev-iteration loop for the Cortex narration component.

Per-day Gemini call breakdown (UTC):
  2026-04-26: 3,034 calls — *3,034 of which returned 404 errors*
              (wrong model endpoint paths during initial development)
  2026-04-27: 8,015 calls (7,664 successful 2xx, 290 404, 20 rate-limited 429)
  2026-04-30:    19 calls

Approximately 3,300 of the 11,035 calls (~30%) were 404 error responses
caused by model-endpoint misconfiguration during my initial integration —
work that produced no usable output and was effectively wasted dev-loop
churn rather than productive use of the API.

Why budgets did not protect me:

I had FOUR billing budgets configured at the time of the spike ($10, $50,
$50, $100). None of them stopped the spend, because GCP budgets are
notification-only by default — they email at the threshold percentages
but do not enforce a hard cap or detach billing automatically. As a
first-time GCP user coming from AWS (where AWS Budgets work the same way,
to be fair), I assumed setting a budget at "100%" meant something would
actually stop. It did not.

The budgets emailed me as designed, but the emails went to spam folder
filters and were not acted on within the spend window.

═══════════════════════════════════════════════════════════════════
PART 4 — IMMEDIATE REMEDIATION TAKEN
═══════════════════════════════════════════════════════════════════

As soon as I noticed the bill, I:

1. Disabled the generativelanguage.googleapis.com API on the project.
2. Disabled the aiplatform.googleapis.com API on the project.
3. Deleted the API key (display name "Snowy", id
   9bfb5927-427e-44d3-b0d1-f625fa1ebaa8) that was tied to Gemini.
4. Deleted the orphaned $50 "Gemma 4 Monthly Budget" that was watching
   a deleted project number.
5. Tightened the account-wide budget from $100 with a 150% threshold
   down to $50 with thresholds at 50/80/90%.
6. Deployed a Cloud Function (budget-killer, us-central1) that
   subscribes to a new Pub/Sub topic (budget-overrun) and detaches
   billing from the project automatically when any budget hits 90%.
   All three remaining budgets now publish to this topic. This means
   the misunderstanding about how budgets work cannot recur — the
   next time spend approaches a cap, billing is automatically
   detached and all chargeable APIs return PERMISSION_DENIED.
7. Created a BigQuery dataset (abm-isu:billing_export) and prepared
   the billing-export-to-BQ pipeline so per-service spend is
   queryable in real time going forward.
8. Migrated all ongoing inference work off Gemini API entirely —
   the project now uses local Ollama on a self-hosted RTX 5090
   (sm_120 Blackwell, 32 GB VRAM) for Gemma 3 inference at zero
   per-call cost. Cloudflare Workers AI is the cloud fallback.
   Gemini is reserved exclusively for the May 18 hackathon final
   submission, where I will use it within the AI Studio free tier
   on a non-billing-enabled key.

═══════════════════════════════════════════════════════════════════
PART 5 — WHAT I'M ASKING FOR
═══════════════════════════════════════════════════════════════════

A) FULL OR PARTIAL REFUND for the Apr 26 – Apr 30 window:
   • The project was 5 days old when the spike happened
   • This is the first chargeable usage ever on this account
   • ~30% of the calls were 404 errors from misconfiguration
   • Good-faith budget alerts were configured (just misunderstood)
   • Immediate full remediation has been deployed

B) ENROLLMENT in the credit programs I'm eligible for under the
   Cortex project's actual circumstances:

   1. Google Cloud Research Credits ($5K, faculty PI route) —
      via [ISU_PROFESSOR_NAMES] at Illinois State University, who can
      apply as the PI for the Cortex/brain-foundation-model research.
      Can you confirm the path and prerequisites here? The university
      research office has agreed to support the application.

   2. Google for Startups Cloud Program — Start tier ($2,000 / 1 year).
      Alexios Bluff Mara LLC qualifies as an early-stage company
      (founded < 5 years ago, no equity funding yet).

   3. Google for Startups — Black Founders Fund (up to $350K credits,
      cash + Cloud benefits). Qualifying as a minority-owned business.

   4. Gemma Hackathon participant credits — if Google DeepMind or
      Kaggle have allocated any per-team credit for the Gemma 4 Good
      hackathon, I'd like to be enrolled.

I'd be happy to provide the formal LLC documentation, AWS certification,
ISU faculty letters, and project plan via secure channels.

Thank you for reviewing this. I'm asking for a fair outcome on the spike
itself, and equally importantly, the right structural setup so that
going forward this project can be a legitimate Chicago-area
academic-industry success story for Google Cloud rather than a
cautionary tale.

Best,

Soumit Lahiri
Founder, Alexios Bluff Mara LLC (dba Red Team Kitchen)
soumitlahiri@philanthropytraders.com  |  Chicago, IL
GCP project: abm-isu  |  Billing account: 01F58E-689697-ADE7FC
```

---

## Programs to apply to in parallel (don't wait on the support case)

| Program | Amount | Path | Approval window |
| --- | --- | --- | --- |
| **Google Cloud Research Credits** | $5K (faculty) / $1K (PhD student) | ISU professor applies as PI, lists you as collaborator. https://edu.google.com/intl/ALL_us/programs/credits/research/ | 4-6 weeks |
| **Google for Startups — Start tier** | $2K credit, 1 year | https://cloud.google.com/startup/apply — self-attest early-stage | 1-2 weeks |
| **Black Founders Fund** | Up to $350K (cash + cloud) | https://startup.google.com/programs/black-founders-fund/united-states/ — minority-owned attestation + business docs | Cohort-based; check current cycle dates |
| **Google for Startups Accelerator: Black Founders (NA)** | Equity-free + cloud + mentorship | https://startup.google.com/programs/accelerator/black-founders/ | Cohort-based |
| **Cloud for Education (per-student)** | $300 / student / year | https://docs.cloud.google.com/billing/docs/how-to/edu-grants — needs ISU institutional sponsorship | Through ISU |
| **Gemma 4 Good Hackathon prize** | $200K total pool | https://www.kaggle.com/competitions/gemma-4-good-hackathon — submit by May 18 2026 | After deadline |

Apply for **Start tier** today (fastest), have the ISU professor open the **Research Credits** application this week, and submit **Black Founders Fund** when their next cohort opens.

Sources:
- [Google Cloud Research Credits — Faculty/PI program](https://edu.google.com/intl/ALL_us/programs/credits/research/)
- [Google Cloud for Researchers overview](https://cloud.google.com/edu/researchers)
- [Google Cloud research-credits application guidelines](https://support.google.com/google-cloud-higher-ed/answer/10724468?hl=en)
- [Education credits redemption docs](https://docs.cloud.google.com/billing/docs/how-to/edu-grants)
- [Google for Startups Cloud Program — overview](https://startup.google.com/cloud/)
- [Google for Startups benefits + tiers](https://cloud.google.com/startup/benefits)
- [Black Founders Fund — US](https://startup.google.com/programs/black-founders-fund/united-states/)
- [Google for Startups Black Founders Accelerator (NA)](https://startup.google.com/programs/accelerator/black-founders/)
- [North America Partner Kickstart 2026 — Chicago office event](https://cloud.google.com/events/kickstart-2026-chicago)
- [Gemma 4 Good Hackathon — Kaggle](https://www.kaggle.com/competitions/gemma-4-good-hackathon)
