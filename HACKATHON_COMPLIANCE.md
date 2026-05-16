# Hackathon Compliance Dossier — The Gemma 4 Good Hackathon

**Owner:** Soumit Lahiri (Alexios Bluff Mara LLC, dba Red Team Kitchen)
**Submission:** Mercury (agent gateway) + Cortex (brain-response prediction backend)
**Track:** Digital Equity & Inclusivity (Mercury primary) — Health & Sciences (Cortex secondary)
**Final Submission Deadline:** 2026-05-18, 23:59 UTC
**Dossier compiled:** 2026-05-06 (12 days remaining)
**Compiled by:** Claude (research agent)
**Authoritative status:** primary sources cited inline; anything I could not directly verify is marked `UNVERIFIED`.

---

## 0. TL;DR — Decisions you need to make this week

1. **Pick ONE track in the Kaggle Writeup form.** A submission can only enter one Track Awards bucket plus an optional Special Technology prize. Recommendation: file **Digital Equity & Inclusivity** as the primary (Mercury's local-first cost-zero story) and **Ollama** as the Special Technology Track (Mercury hits Ollama on the 5090 already; that's a free $10K shot).
2. **Cortex's "Live Demo" + TRIBE v2 (CC-BY-NC) is the single biggest IP risk.** Winning the hackathon = receiving a $10K–$50K cash Prize. Meta's TRIBE v2 license bars commercial use; a paid Prize for a system that *uses* the model is a gray zone (see §3.2). Mitigations are listed below; the safest is to **de-emphasize Cortex's TRIBE-driven inference** in the writeup and showcase Mercury (no TRIBE exposure) as the primary entry.
3. **Winners must license the Submission and source code under CC-BY 4.0** (Kaggle rules §2.5). Mercury is currently MIT (inherited from Hermes); Cortex is Apache-2.0. Both are CC-BY-4.0-compatible (the rules explicitly let you use OSI-approved licenses; CC-BY 4.0 is OSI-approved-equivalent for our purposes since it doesn't limit commercial use). Action: add an explicit `WINNER_LICENSE.md` stating "if selected as a winner, the Submission as-defined-by-Kaggle is granted under CC-BY 4.0" — see §6.
4. **Demo video must be on YouTube, ≤ 3 minutes, public-no-login.** Currently `D:/cortex/demo_videos/cortex_demo_v2_final.mp4` exists locally; it has not been verified to be uploaded.
5. **Pixel 9 ad clips: not actually downloaded.** I checked `D:/cortex/scripts/demo_clips.yaml` — the curated list uses official Google channels (DeepMind Gemma launch, Google I/O, Year in Search, AlphaFold, music generation) with `PLACEHOLDER_*` URLs, all stamped with "official Google channels only" policy. No Pixel 9 ads are committed. The demo videos in the repo (`demo_clip_20s_silent.mp4`, `nasa_artemis_15s_silent.mp4`) appear to be silent NASA Artemis footage. **Confirm before publishing the YouTube cut.**

---

## 1. Kaggle Competition Rules

### 1.1 Authoritative source

Official rules page (full text retrieved 2026-05-06 via Tavily extract):
**https://www.kaggle.com/competitions/gemma-4-good-hackathon/rules**

Overview / submission requirements:
**https://www.kaggle.com/competitions/gemma-4-good-hackathon/overview**

Citation block from the page (must be cited if you reference the competition in publications):

> Ian Ballantyne, Glenn Cameron, María Cruz, Olivier Lacombe, Kristen Quan, and Omar Sanseviero. *The Gemma 4 Good Hackathon.* https://kaggle.com/competitions/gemma-4-good-hackathon, 2026. Kaggle.

### 1.2 Eligibility (verbatim, Foundational Rules §3.1)

To enter you must be:

1. A registered Kaggle.com account holder.
2. The older of 18 years old or the age of majority in your jurisdiction. *(Soumit is 18+.)*
3. **Not** a resident of Crimea, the so-called Donetsk People's Republic (DNR), Luhansk People's Republic (LNR), Cuba, Iran, or North Korea. *(Chicago, IL — clear.)*
4. Not subject to U.S. export controls or sanctions. *(Clear.)*

**Single-account rule (verbatim):**
> "You cannot sign up to Kaggle from multiple accounts and therefore you cannot enter or submit from multiple accounts."

**Entity representation (Foundational §3.1.c):** if you enter on behalf of an entity (Alexios Bluff Mara LLC), the rules bind you *individually and the entity*. You warrant the entity has full knowledge and consents and that participation does not violate entity policies. Action: keep a one-line LLC consent memo in the LLC binder. Soumit is sole member, so this is self-attesting, but a paper trail is cheap insurance.

### 1.3 Team limits and submission limits (verbatim, Competition-Specific §2.1, §2.2)

- **Maximum team size: five (5).**
- **Each Team may submit one (1) Submission only.** ("For Hackathons, each team is allowed one (1) Submission; any Submissions submitted by Participants before merging into a Team will be unsubmitted.")
- Re-edit and re-submit allowed up to deadline; the last-saved-and-submitted state wins.

### 1.4 Submission requirements (verbatim from Overview)

> A valid submission must contain the following:
> 1. Kaggle Writeup
>    1. Attached Public Video
>    2. Attached Public Code Repository
>    3. Attached Live Demo
>    4. Media Gallery

#### 1.4.a Kaggle Writeup
- **≤ 1,500 words.** Submissions over the limit "may be subject to penalty."
- Must select a Track to submit.
- Title, subtitle, detailed analysis required.
- Style: paper-or-blog, "the Proof of Work."

#### 1.4.b Video
- **≤ 3 minutes**, **on YouTube** (other hosts not accepted), **viewable by judges without login**.
- "This is the most important part of your submission."
- Attach to Media Gallery.

#### 1.4.c Public Code Repository
- Public (no login, no paywall).
- Well-documented; "must clearly show the implementation of Gemma 4."
- Linked under "Project Links" in the Writeup attachments.
- A private Kaggle Notebook will *automatically be made public after the deadline* — design your repo with that in mind.

#### 1.4.d Live Demo
- Public URL or files. No login, no paywall.

#### 1.4.e Media Gallery
- Cover image required.

### 1.5 Tracks and prizes (verbatim, $200K total)

| Bucket | Prizes |
|---|---|
| **Main Track ($100K)** | 1st $50K · 2nd $25K · 3rd $15K · 4th $10K |
| **Impact Track ($50K)** | Health & Sciences $10K · Global Resilience $10K · Future of Education $10K · **Digital Equity & Inclusivity $10K** · Safety & Trust $10K |
| **Special Technology Track ($50K)** | Cactus $10K · LiteRT $10K · llama.cpp $10K · **Ollama $10K** · Unsloth $10K |

> *"Projects are eligible to win both a Main Track Prize and a Special Technology Prize."*
> (Impact Track and Main Track interaction is not stated explicitly — `UNVERIFIED` — but the Special Technology language strongly implies all three buckets are independently judged. If you want to confirm, post a question on the discussion forum and screenshot the answer for the file.)

### 1.6 Evaluation rubric (verbatim, weighted)

| Criteria | Points |
|---|---|
| **Impact & Vision** (video-driven) | 40 |
| **Video Pitch & Storytelling** | 30 |
| **Technical Depth & Execution** (verified by code + writeup) | 30 |

> "Your project will be judged primarily on your video demo."

### 1.7 Timeline

- **April 2, 2026** — Start.
- **May 18, 2026, 11:59 PM UTC** — Final Submission Deadline. (T-12 days as of 2026-05-06.)

The org reserves the right to modify the timeline; check the Overview > Timeline tab in the final 72 hours.

### 1.8 Winner license (verbatim, Competition-Specific §2.5.a — load-bearing)

> *"You hereby license and will license your winning Submission and the source code used to generate the Submission under [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.en), an Open Source Initiative-approved license that in no event limits commercial use of such code or model containing or depending on such code."*

> *"For generally commercially available software that you used to generate your Submission that is not owned by you, but that can be procured by the Competition Sponsor without undue expense, you do not need to grant the license in the preceding Section for that software."*

> *"In the event that input data or pretrained models with an incompatible license are used to generate your winning solution, you do not need to grant an open source license in the preceding Section for that data and/or model(s)."*

**Reading:** The §2.5.a.3 carve-out is the escape valve for the TRIBE v2 (CC-BY-NC) and Llama 3.2 (Llama Community License) embedded encoders. We are NOT required to relicense those upstream weights as CC-BY 4.0; we ARE required to license our own code (Mercury, Cortex) as CC-BY 4.0 if we win. CC-BY 4.0 is broader than MIT/Apache 2.0 in some ways and narrower in others — practically, if you're already MIT or Apache 2.0, granting an additional CC-BY-4.0 license to the Sponsor on the same code is non-conflicting (you can dual-license). Add a `WINNER_LICENSE.md`.

### 1.9 Winner's obligations (verbatim, §2.8)

The winner must:

1. Deliver "the final model's software code as used to generate the winning Submission and associated documentation" following the [Kaggle Winning Model Documentation Guidelines](https://www.kaggle.com/WinningModelDocumentationGuidelines). For Hackathons, *"the Submission deliverables will be as described on the Competition Website, which may be information or materials that are not software code."*
2. Grant the §2.5 license and "represent that you have the unrestricted right to grant that license."
3. Sign and return all Prize acceptance documents including:
   - Eligibility certifications;
   - Licenses, releases and other agreements;
   - **U.S. tax forms — IRS Form W-9 (US resident) or W-8BEN (foreign resident).** Soumit is US — W-9 in the LLC's name + EIN.

Documentation must "follow these documentation guidelines, must be capable of generating the winning Submission, and contain a description of resources required to build and/or run the executable code successfully. For avoidance of doubt, delivered software code should include training code, inference code, and a description of the required computational environment."

### 1.10 Data and external tools (verbatim, §2.4 + §2.6)

- **§2.4.a — No competition data is provided.** ("None. Competition Data will not be provided by Competition Sponsor for this Competition.")
- **§2.6.a — External data is allowed**, provided it is "publicly available and equally accessible to use by all Participants of the Competition for purposes of the competition at no cost to the other Participants, OR satisfies the Reasonableness criteria."
- **§2.6.b — Reasonableness Standard:** datasets that exceed the cost of a Prize are NOT reasonable. A small subscription (e.g., Gemini Advanced) is acceptable.
- **§2.6.c — AMLT (Automated ML Tools) allowed** with appropriate license.

### 1.11 Code-sharing rules (Foundational §3.6)

- **Private code sharing outside your team during the competition is forbidden.**
- **Public code sharing is permitted on the Kaggle competition forum/notebooks.** Once shared publicly, it's deemed licensed under an OSI-approved license that does not limit commercial use. (i.e., posting code to the discussion forum makes it MIT-or-broader by operation of the rules.)
- Open-source dependencies must themselves be OSI-approved-license that doesn't limit commercial use.

### 1.12 Prohibitions worth noting

- **§3.4.b — No human labeling/prediction of test data** (irrelevant to a generative hackathon, but the rule exists).
- **§3.14 — Warranty of originality:** Submission must be "your own original work" and not infringe third-party IP, defame, or violate law. You also indemnify the Competition Entities. This is the clause that makes copyrighted Pixel ad footage in your demo video an *active risk*: it would be your liability, not Kaggle's.
- **§3.16 — Sponsor reserves the right to disqualify** for tampering, deception, etc.

### 1.13 Governing law and jurisdiction

- **California law**, exclusive jurisdiction in **Federal or State courts of Santa Clara County, CA.**

---

## 2. Gemma Model License

### 2.1 Critical correction up front

**Gemma 4 is licensed under Apache License 2.0**, **NOT** the custom Gemma Terms of Use. This is stated at the very top of `https://ai.google.dev/gemma/terms`:

> *"The terms below apply to Gemma models listed in the Appendix at bottom of this page. **For Gemma 4 terms, see the Gemma 4 license.**"* (linking to `https://ai.google.dev/gemma/apache_2`)

**Source:** Gemma Terms of Use, last modified 2026-04-01, retrieved 2026-05-06.

The custom Gemma Terms of Use (with the Prohibited Use Policy, "Gemma is provided under and subject to..." Notice file requirement, and Section 3.2 use restrictions that must be passed downstream) apply to Gemma 1, 1.1, 2, 3, 3n, FunctionGemma, EmbeddingGemma, PaliGemma, ShieldGemma, CodeGemma, RecurrentGemma, etc. — **but not Gemma 4.**

**Action — fix existing docs:**
- `D:/cortex/SUBMISSION_COPY.md` line 226 says: *"License: MIT (code) · CC-BY-NC 4.0 (TRIBE v2 weights) · Gemma Terms of Use (Gemma 4)"*. **Change to "Apache 2.0 (Gemma 4)".**
- Same file line 178 says *"Gemma is a trademark of Google LLC."* — keep this; Apache 2.0 §6 (Trademarks) explicitly excludes trademark rights, so the disclaimer is still required.

### 2.2 Apache 2.0 obligations for redistributing Gemma 4 weights or derivatives

If you DO redistribute the weights (we do NOT — we pull from HuggingFace at install time), Apache 2.0 §4 requires:

1. Recipients get a copy of the License.
2. Modified files carry "prominent notices stating that You changed the files."
3. Retain all copyright, patent, trademark, and attribution notices from the source.
4. If a `NOTICE` text file exists in the source, your derivative must include the readable copy of the attribution notices (or a `NOTICE` of your own that includes them).

**Cortex already has `D:/cortex/NOTICE`** with the Gemma 4 Apache-2.0 attribution + trademark disclaimer. Mercury does not — see §3.5.

### 2.3 Embeddings, derivatives, fine-tunes

Apache 2.0 has no equivalent of the custom Gemma TOS "Model Derivatives" clause; you can fine-tune, distill, or modify Gemma 4 freely as long as you preserve attribution and don't claim Google endorsement.

The **Cortex training pipeline** at `D:/cortex/cortex/train_tribe.py` and Tribe-finetuning of Gemma 4 E4B → "Cortex-Tribe" specialist (per `MEMORY.md` and the `cortex_train.jsonl` files) creates a Gemma 4 derivative. Under Apache 2.0 you can call this whatever you want, ship it under whatever license you want **for your own contributions** — but you cannot drop the Apache-2.0 grant or attribution on the upstream weights, and per §3.4 you cannot use Google trademarks, including "Gemma," in the *name* of the new model variant.

### 2.4 The naming guidelines link from the Overview page

The overview links to Google's *External Gemma Model Variant Guidelines*:
**https://ai.google/documents/32/External_Gemma_Model_Variant_Guidelines.pdf**

`UNVERIFIED` — I did not retrieve this PDF directly (gated by Google asset hosting). What is publicly known: derivative model names must not begin with "Gemma" alone; community convention is `<your-name>-gemma-<size>` (e.g., `unsloth/gemma-4-26b-a4b-it-UD-MLX-4bit`, `mlx-community/gemma-4-e4b-it-4bit`). For your pre-trained Cortex specialist, use `redteamkitchen/cortex-tribe-gemma4-e4b` or `alexiosbluffmara/cortex-finetune-gemma4-e4b`, **NOT** "Gemma-Cortex" or "Cortex Gemma." **Action: download and read this PDF in full before publishing the writeup.**

### 2.5 Prohibited Use Policy

Apache 2.0 has no behavioral acceptable-use policy. **However**, Google's *Gemma Prohibited Use Policy* (https://ai.google.dev/gemma/prohibited_use_policy, last modified 2024-08-05) is referenced in the legacy Gemma TOS and remains the de-facto behavioral guideline Google publicizes. For Gemma 4 specifically, Apache 2.0 controls — there is no contractual obligation to follow the Prohibited Use Policy. Practically, follow it anyway: the listed prohibitions (CSAM, illegal-services facilitation, automated decisions in employment/housing/legal/medical, unauthorized practice of professions, etc.) are things you'd never want a Digital Equity submission to do regardless. Cortex's "clinical narration" output already includes population-average disclaimers per `SUBMISSION_COPY.md` lines 214–219; Mercury is plain agent text. Both are clear.

---

## 3. Compatibility Matrix

### 3.1 The matrix

| Component | License | Redistribute? | Compatible with Mercury (MIT)? | Compatible with Cortex (Apache 2.0)? | Compatible with CC-BY-4.0 winner license? |
|---|---|---|---|---|---|
| **Gemma 4 weights** (Apache 2.0) | Apache 2.0 | We do NOT redistribute; pulled at install time. | ✅ | ✅ | ✅ |
| **`mlx-community/gemma-4-*`** (HF) | Apache 2.0 (inherits from upstream) | Redistributed by mlx-community on HF; we link to it. | ✅ | ✅ | ✅ |
| **`unsloth/gemma-4-*`** (HF, e.g., UD-MLX-4bit) | Apache 2.0 (Unsloth republishes under same upstream license) | Same. | ✅ | ✅ | ✅ |
| **`lmstudio-community/gemma-4-*`** (HF GGUF) | Apache 2.0 (inherits) | Same. | ✅ | ✅ | ✅ |
| **TRIBE v2** (Meta, CC-BY-NC 4.0) | CC-BY-NC 4.0 (verified at github.com/facebookresearch/tribev2/blob/main/LICENSE) | We do NOT redistribute weights; Cortex pulls them. | ⚠️ Mercury MIT permits, but TRIBE itself bars commercial use of derivatives. | ⚠️ Same. | ⚠️ Carved out by Kaggle §2.5.a.3 — not required to relicense — BUT see §3.2. |
| **V-JEPA 2** (encoder inside TRIBE v2, CC-BY-NC 4.0) | CC-BY-NC 4.0 | Indirect use through TRIBE. | ⚠️ Same. | ⚠️ Same. | ⚠️ Same. |
| **wav2vec-BERT 2.0** (audio encoder inside TRIBE v2, MIT) | MIT | Indirect. | ✅ | ✅ | ✅ |
| **Llama 3.2 3B** (text encoder inside TRIBE v2, Llama 3.2 Community License) | Llama 3.2 Community License | Indirect. Has its own Acceptable Use Policy + 700M-MAU clause. | ⚠️ Compatible with MIT for distribution but adds restrictions. | ⚠️ Same. | ⚠️ Carved out by §2.5.a.3. |
| **Schaefer 400 atlas** (Yeo Lab, CC-BY 4.0) | CC-BY 4.0 | We can redistribute with attribution. | ✅ | ✅ | ✅ |
| **Hermes-Agent (Mercury upstream, Nous Research)** | MIT | We forked. | ✅ (Mercury inherits MIT) | n/a | ✅ (MIT → CC-BY 4.0 dual-grant on our additions is fine) |
| **Cortex code** | Apache 2.0 | Public on GitHub. | n/a | self | ✅ |
| **Mercury code** | MIT (Nous header retained) | Public on GitHub. | self | n/a | ✅ |

### 3.2 The TRIBE v2 commercial-use question (this is the real one)

**Setup.** TRIBE v2 weights are CC-BY-NC 4.0. CC-BY-NC §1(i) defines "NonCommercial" as *"not primarily intended for or directed towards commercial advantage or monetary compensation."* §2(a)(1)(b) grants permission to *"produce, reproduce, and Share Adapted Material for NonCommercial purposes only."*

**The hackathon prize.** Winning the Health & Sciences track yields $10K cash; Main Track 1st is $50K. A cash prize from a competition is monetary compensation paid to the entrant. The question is whether *the act of producing the Submission for the competition* is "primarily directed towards monetary compensation."

**My read** (not legal advice — Soumit should run this past a Chicago attorney before banking on it):

1. **Pure inference at demo time, weights not redistributed, no payment for the inference itself, no commercial product on top:** likely OK. CC-BY-NC permits non-commercial *use*; running inference on a free demo for a hackathon submission is consistent with the kind of "research use" Meta itself cites in TRIBE v2's release blog post.
2. **Receiving prize money for a Submission that depends on TRIBE v2:** gray. The Submission is a hackathon entry, not a sale of a TRIBE-v2-derived product. The prize is awarded by Google to the *entrant*, not paid by users to the entrant. But a strict reading of "directed towards monetary compensation" can sweep this in.
3. **Running `cortex.redteamkitchen.com` as a paid SaaS or commercial service:** clearly forbidden by CC-BY-NC. Today the demo is free; the Mercury Cost Analysis writeup lists $0/month ongoing — so we are not commercial in the SaaS sense.
4. **Redistributing TRIBE v2 weights or a TRIBE-derivative as part of the Submission code repo:** forbidden if Cortex's repo were sold for money or behind a paywall. We don't redistribute weights — Cortex pulls them at runtime. So the §2.5.a.3 Kaggle carve-out applies cleanly: TRIBE v2 has an "incompatible license" and we do not need to grant an open-source license on those weights.

**Recommended mitigation, in order of how much it costs us:**

- **Mitigation A (free):** In the writeup and the README, state explicitly that *Cortex inference is offered free of charge for non-commercial demonstration* and that the project does not redistribute TRIBE v2 weights. This is consistent with the CC-BY-NC license and matches what Cortex actually does.
- **Mitigation B (free, recommended for the writeup):** Lead with **Mercury** as the primary submission. Mercury is MIT-licensed code that calls Gemma 4 (Apache 2.0). It does not depend on TRIBE v2 — Cortex is a *separate* product Mercury can talk to but doesn't require. Filing Mercury as the Digital Equity entry shifts the IP-risk surface entirely off TRIBE.
- **Mitigation C (free, on top of A+B):** Add a "Commercial use" section to the Cortex README clarifying that **commercial deployments require a separate license from Meta** for TRIBE v2, and provide the contact route from `https://huggingface.co/facebook/tribev2`. This is what Meta wants to see and demonstrates good-faith awareness.
- **Mitigation D (paid):** Email Meta FAIR via the contact on the TRIBE v2 HF page and ask whether participation in the Gemma 4 Good Hackathon (with the Kaggle CC-BY 4.0 winner-license carve-out for incompatible upstream models) constitutes a permitted use. Get a yes-or-no in writing. ~zero cost, ~5 days; do this NOW (T-12 days).

### 3.3 The "we use Gemma 4 but don't redistribute weights" question

Apache 2.0 imposes redistribution-time obligations (NOTICE, copy of license, modified-file notices). When you don't redistribute, you don't trigger those obligations.

**However**, Apache 2.0 §4 also requires preserving copyright/attribution notices on any *Derivative Works* you redistribute. Cortex's `cortex_train.jsonl` and `cortex_train_v2.jsonl` are training data for a Gemma 4 fine-tune, NOT Gemma 4 itself. The fine-tune *weights* (if shipped as e.g. an Ollama tag `tribe` per `SUBMISSION_GEMMA4.md` line 130) ARE a derivative work and DO trigger Apache 2.0 attribution. If the fine-tune is published to HuggingFace or Ollama, it must carry the Apache-2.0 license file + a copy of the upstream Gemma 4 model card or equivalent attribution.

**Action:** Before publishing any Cortex fine-tune of Gemma 4 publicly, generate a model card with:
- Apache-2.0 license declaration
- Upstream model: `google/gemma-4-e4b-it` (or whichever)
- "This is a derivative of Gemma 4 by Google LLC. Gemma is a trademark of Google LLC. This model is not endorsed by Google."
- Training data license + sourcing.
- Intended use + caveats.

### 3.4 The combined picture

| Submission style | Risk | Recommendation |
|---|---|---|
| Mercury alone, Digital Equity track, Gemma 4 only | Low | **Default. Submit this.** |
| Mercury + Cortex demo as supporting evidence, single Writeup | Medium (TRIBE v2 in the demo) | OK if the demo is free, weights not redistributed, NOTICE clean. Apply Mitigation C. |
| Cortex as a standalone Health & Sciences submission | Medium-high | Only if Mitigation D email comes back positive. |
| Submitting BOTH Mercury (Digital Equity) and Cortex (Health & Sciences) as separate Writeups | **Forbidden by §2.2.a** ("each Team may submit one (1) Submission only") | Don't do it. |

### 3.5 Mercury MIT vs. CC-BY 4.0 winner license

MIT permits relicensing-at-distribution as long as the original copyright + permission notice are preserved. The winner license under Kaggle §2.5.a is a *grant to the Sponsor*, not a relicensing of the Mercury repo as a whole. We can:

1. Keep `LICENSE` as MIT (preserving the Nous Research copyright, since Mercury is a fork).
2. Add `WINNER_LICENSE.md` granting the Sponsor a CC-BY 4.0 license on the Submission and the source code as defined by Kaggle.

Both grants exist simultaneously; they don't conflict because (a) MIT permits sublicensing under any terms, and (b) CC-BY 4.0 permits commercial use, which MIT also permits.

---

## 4. Demo / Video / Screenshot Rights

### 4.1 What's required

Per the Overview: video must be on YouTube, ≤ 3 minutes, public-no-login, "tell a story." Per §3.14, the Submission must not infringe third-party IP — and "Submission" includes the video as an attached deliverable.

### 4.2 Pixel 9 ad footage — actual state

The user's question references "4 Pixel 9 ads with yt-dlp." I checked `D:/cortex/scripts/demo_clips.yaml` (49 lines, last modified per repo) — the curated list is:

| id | description | url |
|---|---|---|
| deepmind-gemma-launch | Google DeepMind — Gemma launch announcement | `PLACEHOLDER_DEEPMIND_GEMMA` |
| io-keynote-ai | Google I/O — AI keynote opening | `PLACEHOLDER_IO_AI_KEYNOTE` |
| year-in-search | Year in Search 2025 | `PLACEHOLDER_YEAR_IN_SEARCH` |
| deepmind-protein-folding | AlphaFold — protein structure narrative | `PLACEHOLDER_PROTEIN_FOLDING` |
| gemma-multimodal-demo | Gemma 4 multimodal demo | `PLACEHOLDER_GEMMA_MULTIMODAL` |
| deepmind-music-generation | DeepMind music generation showcase | `PLACEHOLDER_DEEPMIND_MUSIC` |

The YAML's source policy comment reads:

> *"Source policy: official Google channels only — Google I/O keynotes, Google DeepMind research videos, Gemma announcements, Year in Search. These are public on YouTube under YouTube's standard license; using them in a hackathon demo of a Gemma-built tool is on-brand and copyright-safe."*

**The URLs are placeholders.** No actual Pixel 9 ad clips were committed in the curated list. The committed assets in `D:/cortex/assets/` are `demo_clip_20s_silent.mp4` and `nasa_artemis_15s_silent.mp4` (NASA Artemis is U.S. Government work — public domain in most uses). The committed videos in `D:/cortex/demo_videos/` are Cortex-produced demos (`cortex_demo_*.mp4`) showing the visualizer, not third-party ads.

**Action: search the local cache for any Pixel-9-ad MP4 files before publishing.**
```bash
find D:/cortex/ -iname "*pixel*" -o -iname "*ad*.mp4" 2>/dev/null
```
If anything turns up that originated from a Pixel ad on YouTube, do not include it in the published cut.

### 4.3 What you CAN safely use in the YouTube demo cut

In descending order of safety:

1. **Your own Cortex/Mercury UI screen recordings** — fully owned by Soumit/the LLC. **Use these.**
2. **NASA imagery / Artemis footage** — U.S. government work; almost always public-domain or near-public-domain (NASA Media Usage Guidelines: free for non-misleading use, no NASA endorsement implied). Already in the repo. **Use these.**
3. **Official Google Gemma launch / DeepMind / I/O clips** posted to Google's official YouTube channels — uploaded under YouTube's Standard License. Embedding (via YouTube's player) is always allowed. **Re-uploading clips into YOUR YouTube video** is *not* automatically permitted by the Standard License; it is a derivative work. Treat it as fair use (commentary on the model your tool uses) — defensible but not bulletproof. If you must include such clips, keep them brief (≤ 10 s each), include on-screen attribution ("Source: Google DeepMind, https://youtube.com/..."), and frame them as commentary.
4. **CC0 stock footage** (Pexels, Pixabay, Mixkit) — explicit free-for-commercial license. Safe.
5. **CC-BY stock footage** — fine if you credit in the video credits.
6. **Pixel 9 (or any consumer-product) ad footage** — copyrighted by Google's marketing team. **Avoid in the published cut.** If the demo absolutely requires showing a Pixel ad as a stimulus to TRIBE v2, do it locally (not in the YouTube upload) and substitute with a CC0 stock clip in the video.

### 4.4 Music in the YouTube cut

YouTube's Content ID will mute or strike copyrighted music. Use:

- YouTube Audio Library (free, monetization-safe)
- Mixkit / Pixabay free music
- Your own composition

The DeepMind music-generation showcase clip (id `deepmind-music-generation` in the YAML) — if that's actual generated audio, it's licensed under YouTube's Standard License; same caveat as §4.3.3 above. Don't put 30+ seconds of it under your demo voiceover.

### 4.5 Screenshots

Screenshots of:
- **Your own UI** — fine.
- **HuggingFace model pages** — fair use for documentation; HF allows it.
- **Kaggle competition page** — fair use; treat as editorial reference.
- **Google product UIs (e.g., Pixel Fold home screen, Gmail)** — fair use as illustrative, but do not imply endorsement. The trademark disclaimer in `D:/cortex/NOTICE` ("Cortex is not endorsed by Google.") covers this; mirror that line in Mercury's README.

### 4.6 People in the video

If the demo video shows Soumit or any other person on camera, they must consent. Soumit on camera is self-consent. If anyone else (Mangolika, Sally, Rosangela, kids in a classroom) appears, get a written release before upload. ISU campus footage *of buildings* is generally fine; *of identifiable students* requires releases.

---

## 5. Dataset Licensing

### 5.1 What Cortex actually uses today

Inspecting `D:/cortex/data/`:

- `cortex_train.jsonl`, `cortex_train_v2.jsonl`, `regen_v2.jsonl` — Cortex's *own* training data, generated synthetically (system-prompt-format JSONL conversations about brain regions). The system prompt and the assistant turns reference TRIBE v2 outputs. These files are **Soumit-authored / synthetic**; license is whatever Soumit declares, and they are part of the Cortex Apache-2.0 repo.
- `dataset_quality_report.md`, training logs — internal artifacts.

I did NOT find evidence in the repo that NSD (Natural Scenes Dataset) raw fMRI volumes or OpenNeuro BIDS datasets are bundled. TRIBE v2 was *trained by Meta* on 1,100+ hours of fMRI from 720 subjects (per Meta's TRIBE v2 release blog) — that training is Meta's legal exposure, not ours. Cortex consumes TRIBE v2 *outputs*, not its training data.

### 5.2 If you DO end up using NSD or OpenNeuro datasets in the final demo

| Dataset | License | Notes for hackathon use |
|---|---|---|
| **OpenNeuro datasets (general)** | CC0 by default — every newly published OpenNeuro dataset since ~2018 is CC0 Public Domain Dedication. Check each individual dataset's `dataset_description.json`. | CC0 = no restrictions. **Allowed.** |
| **Natural Scenes Dataset (NSD)** | NOT CC0. Has separate "NSD Data Access Agreement" — must be signed via the NSD Data Manual (see https://naturalscenesdataset.org). Source: NSD documentation, OpenNeuro FAQ. | The Data Access Agreement typically restricts redistribution and may have additional clauses about acknowledgment. **`UNVERIFIED` whether competition use is explicitly forbidden — read the agreement before you sign it. Skip NSD in the published demo unless you've already signed and the terms permit competition use.** |
| **fMRIPrep outputs** | fMRIPrep is BSD-3-Clause software; the *output* preprocessed data inherits the license of the *input* dataset. So NSD-via-fMRIPrep is still NSD-licensed. | Same caveats as NSD. |
| **HCP (Human Connectome Project)** | HCP Open Access Data Use Terms; requires registration; restricts commercial use. | Avoid; would expose to commercial-use risk like TRIBE v2. |
| **CamCAN, IBC, NaturalImage, Forrest Gump dataset** | CC-BY 4.0 or CC-BY-NC; varies. Each has its own page. | Verify per dataset. CC-BY ✅; CC-BY-NC same caveats as TRIBE. |

**Recommendation for the demo:** Stick with TRIBE v2 *predictions* (which are model outputs, not raw fMRI) plus Cortex's synthetic training data plus NASA / your own footage. Don't open the dataset-licensing can on hackathon timeline.

### 5.3 "Competition use forbidden" licenses to avoid

- Anything CC-BY-NC-ND (no derivatives)
- HCP Restricted Data Use Agreement
- Anything requiring per-publication approval from the data owner
- Anything with a 700M-MAU clause that doesn't apply to you (e.g., Llama 3.2 — but you're nowhere near 700M MAU, so it doesn't bite)

---

## 6. Required Attribution + Boilerplate

### 6.1 GitHub repo README — minimum required content

Both `mercury` and `cortex` README.md files must include:

```markdown
## Acknowledgements & licenses

This project uses **Gemma 4** by Google LLC under the [Apache License 2.0](https://ai.google.dev/gemma/apache_2). Gemma is a trademark of Google LLC. This project is not endorsed by, sponsored by, or affiliated with Google.

[For Cortex only:] This project additionally uses **TRIBE v2** by Meta Platforms, Inc. under [CC-BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). Cortex is offered free of charge for non-commercial demonstration. Commercial deployments require a separate license from Meta. Source: https://github.com/facebookresearch/tribev2

This project is a Submission to *The Gemma 4 Good Hackathon* (Kaggle × Google DeepMind, 2026). Citation:

> Ian Ballantyne, Glenn Cameron, María Cruz, Olivier Lacombe, Kristen Quan, and Omar Sanseviero. *The Gemma 4 Good Hackathon.* https://kaggle.com/competitions/gemma-4-good-hackathon, 2026. Kaggle.
```

### 6.2 Kaggle Writeup — minimum required content

The writeup body should:

1. Open with track selection (Digital Equity & Inclusivity).
2. Describe the problem, vision, architecture, and Gemma-4-specific technical decisions.
3. Include the Acknowledgements block from §6.1.
4. End with:
   ```
   License: this Submission is granted to the Competition Sponsor under CC-BY 4.0 per the Gemma 4 Good Hackathon Official Rules §2.5.a.
   Code under MIT (Mercury) and Apache 2.0 (Cortex), both compatible with the CC-BY 4.0 winner license carve-out for upstream incompatibly-licensed components.
   Gemma is a trademark of Google LLC.
   ```
5. Word count ≤ 1500.

### 6.3 Demo video credits (last 5–10 seconds)

```
Built with Gemma 4 (Google LLC, Apache 2.0)
[For Cortex segments only:] Brain encoding by TRIBE v2 (Meta, CC-BY-NC 4.0)
NASA / Artemis footage: NASA, public domain
Music: [exact YouTube Audio Library track or CC0 source]
Cortex / Mercury © 2026 Alexios Bluff Mara LLC
Submission: The Gemma 4 Good Hackathon
Gemma is a trademark of Google LLC. Not endorsed by Google.
```

### 6.4 Submission form (Kaggle UI)

The Kaggle Writeup form requires:
- **Title** (≤ ~80 chars). Suggestion: *"Mercury: $0/month Gemma 4 agent for any classroom, any device"*
- **Subtitle** (≤ ~120 chars). Suggestion: *"Local-first AI tutoring on a teacher's MacBook. Apache 2.0 weights. 7 surfaces. 0 API spend."*
- **Track** dropdown — pick **Digital Equity & Inclusivity**.
- **Cover image** — required. Make a 1280×720 PNG with the Mercury logo + Gemma + the slogan.

---

## 7. Open-Source Posture

### 7.1 Required by Kaggle?

Yes for winners. Per §2.5.a.1: *"You hereby license and will license your winning Submission and the source code used to generate the Submission under [CC-BY 4.0]."*

For non-winners: nothing in the rules requires open-source. However, §1.4.b ("Public Code Repository … publicly accessible and not require a login or paywall") is a *submission-eligibility* requirement, not a licensing one — your repo must be public to read, but you don't have to license it as OSS unless you win.

### 7.2 Does our current MIT + Apache 2.0 + (Gemma 4 Apache 2.0) + (TRIBE v2 CC-BY-NC) mix qualify?

For **submission eligibility** (everyone): yes, both repos are public on GitHub. ✅

For **winning eligibility** (if we win): we must grant a CC-BY 4.0 license on our own code. The §2.5.a.3 carve-out covers TRIBE v2 / Llama 3.2 — we don't have to relicense those.

**Concretely:**
- Mercury (MIT) — already OSI-approved-equivalent, commercial use allowed. Add a `WINNER_LICENSE.md` granting CC-BY 4.0 on top of MIT for the Submission.
- Cortex (Apache 2.0) — same posture; add `WINNER_LICENSE.md`.
- Gemma 4 weights — Apache 2.0 by Google, fine.
- TRIBE v2 weights — CC-BY-NC, carved out of §2.5.

### 7.3 What about "the model itself"

The §2.5 winner-license language says *"such code or model containing or depending on such code."* If we trained a Cortex-fine-tuned Gemma 4 derivative, it would be a "model containing or depending on such code." Per the carve-out for incompatible upstream models (§2.5.a.3), if the fine-tune depends on a CC-BY-NC component (TRIBE v2 outputs as training labels?), we don't have to grant CC-BY 4.0. **But** the upstream Gemma 4 weights are Apache 2.0, which is compatible with CC-BY 4.0, so the fine-tune itself can be granted CC-BY 4.0 cleanly.

If `cortex_train.jsonl` was generated using TRIBE v2 outputs, then a Cortex fine-tune of Gemma 4 inherits CC-BY-NC training-data lineage → can claim §2.5.a.3 carve-out. If it was generated by a frontier API (Anthropic, GPT, Gemini), the API ToS may have its own restrictions on using outputs to train competing models — `UNVERIFIED`; check the Anthropic ToS specifically since that's how this dossier is being assembled. (Anthropic's commercial ToS as of 2025-09 does NOT restrict using Claude outputs to train your own non-Anthropic models in most cases, but verify.)

### 7.4 Recommended action

Create `D:/mercury/WINNER_LICENSE.md` and `D:/cortex/WINNER_LICENSE.md`:

```markdown
# Winner License Grant

If this repository's contents are selected as a winning Submission in *The Gemma 4 Good Hackathon* (Kaggle × Google DeepMind, 2026), Alexios Bluff Mara LLC (dba Red Team Kitchen) hereby grants the Competition Sponsor a license to the Submission and the source code used to generate the Submission under the [Creative Commons Attribution 4.0 International License](https://creativecommons.org/licenses/by/4.0/), per Gemma 4 Good Hackathon Competition-Specific Rules §2.5.a.

This grant is in addition to, and does not replace, the project's primary license:

- Mercury: MIT License (see LICENSE)
- Cortex: Apache License 2.0 (see LICENSE)

Per §2.5.a.3, this CC-BY 4.0 grant does NOT extend to:

- TRIBE v2 model weights (Meta Platforms, CC-BY-NC 4.0)
- V-JEPA 2 model weights (Meta Platforms, CC-BY-NC 4.0)
- Llama 3.2 weights (Meta Platforms, Llama 3.2 Community License)
- Gemma 4 model weights (Google LLC, Apache 2.0 — already CC-BY-4.0-compatible)

Soumit Lahiri
Sole Member, Alexios Bluff Mara LLC
2026-05-XX
```

---

## 8. Stress-Test / "Show Your Work" Posture

Kaggle expects (§2.8.a + Winning Model Documentation Guidelines):

1. **Reproducibility of the winning Submission.** Code that *can be run* by the Sponsor to reproduce the result.
2. **Description of the computational environment.** Specifically named: training code, inference code, hardware/OS, dependencies.
3. **A README with install steps that work on a fresh machine.**
4. **Pinned dependencies.** This is industry best practice for any reproducibility claim and is implicit in "capable of generating the winning Submission."

Mercury currently advertises (`SUBMISSION_GEMMA4.md` lines 159–169):

```bash
pip install --user git+https://github.com/AlexiosBluffMara/mercury
ollama pull gemma4:e4b
mercury -z "What's 2+2 and why?"
```

Three commands. Good. **Verify each works on a fresh machine before deadline.** Specifically: does `pip install` actually pull the right pinned deps, does `ollama pull gemma4:e4b` resolve in May 2026, does `mercury -z` work without an API key?

For Cortex, the README at `D:/cortex/README.md` should have an equivalent three-to-five-command quick-start. Verify.

**Pin dependencies:** Mercury's `pyproject.toml` and Cortex's `pyproject.toml` should use exact versions or at least minor-pinned (`~=`) versions for `mlx-vlm`, `unsloth`, `ollama`, `httpx`, etc. As of 2026-05-06, MLX-vlm is on 0.5.0 per `SUBMISSION_GEMMA4.md` — pin it.

**`requirements.txt` / `requirements.lock`:** add a frozen lockfile (`uv pip compile pyproject.toml -o requirements.lock`). Per user's `feedback_use_uv.md` rule, use `uv` not pip.

**Hardware profile description:** add to README:
- Mercury developed and tested on: Windows 11 RTX 5090 (32 GB GDDR7), macOS M4 Max (48 GB unified), Raspberry Pi 5 (8 GB). Minimum: anything that can run Ollama + Gemma 4 E2B (5 GB).
- Cortex developed and tested on: Windows 11 RTX 5090 (32 GB GDDR7) for TRIBE v2 + Gemma 4 E4B; M4 Max for cloud-fallback inference. Minimum: 24 GB VRAM CUDA or 32 GB unified macOS for full pipeline.

**Build evidence the judges can sanity-check:**
- Mercury repo already has a `BENCHMARKS.md` and a `kimi_proof/` directory — keep these.
- Cortex submission copy lists 194 tok/s, 75 tok/s MTP, etc. — make sure these numbers are *reproducible*, not just `SUBMISSION_GEMMA4.md`-asserted. Add a `bench/` directory with the runnable script.

**Sanity-check checklist (last 48 hours):**

- [ ] Fresh-clone Mercury into a temp dir and run the 3-command install end-to-end.
- [ ] Fresh-clone Cortex and run its quick-start.
- [ ] Browser-test all linked URLs (mercury.redteamkitchen.com, cortex.redteamkitchen.com, inference.redteamkitchen.com) from a non-Tailscale network.
- [ ] Verify YouTube video is public (incognito browser, no Google login).
- [ ] Verify GitHub repos are public (incognito).

---

## 9. Twelve-Day Checklist (T-12 → T-0, dependency-ordered)

**Status legend:** ✅ DONE · 🟡 IN-PROGRESS · ⬜ NOT-STARTED · ⚠️ NEEDS-DECISION

### Phase 1 — IP cleanup (T-12 to T-9, do FIRST)

1. ⬜ **Email Meta FAIR re: TRIBE v2 + Gemma 4 hackathon use.** Contact via huggingface.co/facebook/tribev2 or the FAIR research-collaboration channel. Ask: *"Does using TRIBE v2 for inference in a Submission to Kaggle's Gemma 4 Good Hackathon, where prizes are awarded by Google to entrants, qualify as a permitted non-commercial use under CC-BY-NC 4.0? We do not redistribute TRIBE v2 weights and offer no paid services on top."* Save the reply.
2. ⬜ **Read the External Gemma Model Variant Guidelines PDF** at https://ai.google/documents/32/External_Gemma_Model_Variant_Guidelines.pdf. Confirm any name/branding rules for fine-tunes.
3. ⬜ **Fix `D:/cortex/SUBMISSION_COPY.md` line 226** — replace "Gemma Terms of Use (Gemma 4)" with "Apache 2.0 (Gemma 4)".
4. ⬜ **Add `D:/mercury/NOTICE`** with the Gemma 4 Apache-2.0 attribution + trademark disclaimer (mirror Cortex's). Mercury currently has no NOTICE file.
5. ⬜ **Add `D:/mercury/WINNER_LICENSE.md`** and `D:/cortex/WINNER_LICENSE.md` per §7.4.
6. ⬜ **Add `Acknowledgements & Licenses` section to both READMEs** per §6.1.
7. ⬜ **Search the local repo for any Pixel-9-ad MP4 and remove from any cut intended for YouTube.** `find D:/cortex/ D:/mercury/ -iname "*pixel*" -o -iname "*ad_*.mp4"`.

### Phase 2 — Submission picks (T-9 to T-7)

8. ⬜ **Decide: Mercury (Digital Equity) or Cortex (Health & Sciences) as the single submission?** Recommendation: **Mercury**. Cortex remains a supporting demo linked from the Mercury writeup as "what Mercury orchestrates." This sidesteps TRIBE v2 IP exposure for the Submission itself.
9. ⬜ **Confirm Special Technology track**. Recommendation: **Ollama** ($10K). Mercury hits Ollama on the 5090 already.
10. ✅ Both repos public on GitHub (`AlexiosBluffMara/mercury`, `AlexiosBluffMara/cortex`) — confirmed via `SUBMISSION_GEMMA4.md`.
11. 🟡 **Mercury writeup at `D:/mercury/SUBMISSION_GEMMA4.md` (~1700 words today).** Trim to ≤ 1500 words for the Kaggle Writeup form. Save the long version separately.

### Phase 3 — Reproducibility (T-7 to T-4)

12. ⬜ **Pin all Python deps with `uv pip compile`** in both repos.
13. ⬜ **Run the 3-command Mercury quickstart on a fresh Windows VM** (or WSL fresh user). Document any gaps.
14. ⬜ **Add a `BENCH.md` to Mercury** listing the 194 tok/s, 75 tok/s MTP, 94 tok/s vanilla numbers with the exact commands to reproduce.
15. ⬜ **Capture a hardware-profile screenshot** showing `nvidia-smi` on Seratonin and `system_profiler SPHardwareDataType` on Big Apple — anchor the "$0/month, runs on a teacher's laptop" claim.

### Phase 4 — Demo video (T-7 to T-2)

16. 🟡 **Cortex demo videos exist locally** (`D:/cortex/demo_videos/cortex_demo_v2_final.mp4` and others). Confirm one is the canonical 3-minute cut.
17. ⬜ **Verify no copyrighted ad footage (Pixel, Apple, Samsung, etc.) is in the YouTube cut.** Substitute with NASA/CC0 stock if needed.
18. ⬜ **Verify all music in the cut is YouTube-Audio-Library or CC0.**
19. ⬜ **Add credits per §6.3.**
20. ⬜ **Upload to YouTube as Public, no age gate, no login required.** Test in incognito on a phone.
21. ⬜ **Capture YouTube link.** Embed in the Kaggle Writeup attachments.

### Phase 5 — Live demo (T-5 to T-2)

22. ✅ `inference.redteamkitchen.com`, `mercury.redteamkitchen.com`, `cortex.redteamkitchen.com` exist per `SUBMISSION_GEMMA4.md`.
23. ⬜ **Test all three from a non-Tailscale network** (mobile data on Pixel Fold) — confirm public reachability.
24. ⬜ **Browser-test every link in the writeup** (per the user's "click, don't curl" rule). Use Claude in Chrome or Claude Preview MCP. Read browser console for errors.
25. ⬜ **Mobile-viewport test** at 390×844 — ensure the demo is operable on a phone.

### Phase 6 — Kaggle submission (T-2 to T-0)

26. ⬜ **Create the Writeup on Kaggle.** Pick the track. Save draft.
27. ⬜ **Upload cover image (1280×720 PNG).**
28. ⬜ **Attach YouTube video, GitHub repo links, live demo URL.**
29. ⬜ **Final review against §1.4 checklist** — Writeup ≤ 1500 words, video ≤ 3 min, code public, demo public, cover image present.
30. ⬜ **Click Submit** before May 18 23:59 UTC. Recommended actual submit time: **May 17 18:00 CDT** (= May 17 23:00 UTC, 25-hour buffer).
31. ⬜ **Screenshot the submission confirmation page** for the LLC binder.

### Phase 7 — Post-submission (T+0 to T+30)

32. ⬜ **Hold the W-9 in the LLC binder ready** in case of prize notification.
33. ⬜ **Monitor Kaggle email** — winners are notified by email; one-week response window per §3.8.b.

---

## 10. Outstanding `UNVERIFIED` items for Soumit to confirm

| # | Item | How to resolve | Cost |
|---|---|---|---|
| U1 | Whether Main / Impact / Special Technology Tracks are independently judged or mutually exclusive | Post on Kaggle discussion forum, screenshot the official answer | Free, ~24 h |
| U2 | External Gemma Model Variant Guidelines PDF — actual naming rules | Download the PDF from https://ai.google/documents/32/External_Gemma_Model_Variant_Guidelines.pdf and read | Free, ~30 min |
| U3 | Meta FAIR's blessing of TRIBE-v2-in-hackathon | Email FAIR per §3.2 Mitigation D | Free, ~3-7 days |
| U4 | NSD Data Access Agreement competition-use clause (only relevant if NSD is used) | Read the Data Manual at naturalscenesdataset.org | Free |
| U5 | Anthropic Claude commercial ToS clause about training data derived from Claude outputs | Read https://www.anthropic.com/legal/commercial-terms | Free |
| U6 | Whether Llama 3.2 Community License's 700M-MAU clause applies to anything we do (it doesn't, but write it down) | Read the LCL once and file the conclusion | Free |

---

## 11. Sources

- Kaggle, *The Gemma 4 Good Hackathon — Official Rules*, https://www.kaggle.com/competitions/gemma-4-good-hackathon/rules (retrieved 2026-05-06)
- Kaggle, *The Gemma 4 Good Hackathon — Overview*, https://www.kaggle.com/competitions/gemma-4-good-hackathon/overview (retrieved 2026-05-06)
- Google, *Gemma Terms of Use* (last modified 2026-04-01), https://ai.google.dev/gemma/terms — establishes that Gemma 4 is governed by the Apache 2.0 license, NOT the custom Gemma TOS
- Google, *Gemma 4 / Apache License 2.0*, https://ai.google.dev/gemma/apache_2
- Google, *Gemma Prohibited Use Policy* (last modified 2024-08-05), https://ai.google.dev/gemma/prohibited_use_policy — applies to legacy Gemma; Gemma 4 only inherits via voluntary best-practice
- Apache Software Foundation, *Apache License 2.0*, https://www.apache.org/licenses/LICENSE-2.0
- Meta Platforms, *TRIBE v2 LICENSE — CC-BY-NC 4.0*, https://github.com/facebookresearch/tribev2/blob/main/LICENSE (retrieved 2026-05-06)
- Meta AI, *Introducing TRIBE v2 — A Predictive Foundation Model*, https://ai.meta.com/blog/tribe-v2-brain-predictive-foundation-model/ (release date 2026-03-26)
- Creative Commons, *CC-BY-NC 4.0 deed*, https://creativecommons.org/licenses/by-nc/4.0/
- Creative Commons, *CC-BY 4.0 deed*, https://creativecommons.org/licenses/by/4.0/
- Kaggle, *Winning Model Documentation Guidelines*, https://www.kaggle.com/WinningModelDocumentationGuidelines
- OpenNeuro, *Frequently Asked Questions* (CC0 default), https://docs.openneuro.org/faq
- AWS Open Data Registry, *Natural Scenes Dataset*, https://registry.opendata.aws/nsd/
- Google AI, *External Gemma Model Variant Guidelines*, https://ai.google/documents/32/External_Gemma_Model_Variant_Guidelines.pdf (UNVERIFIED — to be read)
- YouTube Help, *Fair use on YouTube*, https://support.google.com/youtube/answer/9783148

---

*This dossier is a research aid, not legal advice. The biggest dollar-value decisions (Mitigation D email to Meta, the Track choice, the LLC's W-9 readiness) should be reviewed by Soumit before action; for the TRIBE-v2 commercial-use question specifically, a 30-minute consult with a Chicago IP attorney would close the loop. Total estimated cost to fully de-risk the submission: ~$0–$300 in legal time, ~6 hours of cleanup work spread over T-12 to T-2.*
