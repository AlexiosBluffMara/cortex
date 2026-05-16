# Winner License Grant — Cortex

This document is provided in compliance with the **Gemma 4 Good
Hackathon** Competition-Specific Rules §2.5.a, which require winning
Submissions to be licensed under the
[Creative Commons Attribution 4.0 International (CC-BY 4.0)](https://creativecommons.org/licenses/by/4.0/deed.en)
license.

## What is licensed under CC-BY 4.0 if Cortex wins

In the event that Cortex is selected as a winner of the Gemma 4 Good
Hackathon, Alexios Bluff Mara LLC (dba Red Team Kitchen) hereby
**dual-licenses** all original Cortex source code in this repository
(currently licensed under Apache License 2.0) **additionally** under
CC-BY 4.0 to the Competition Sponsor and the public, effective from
the date of award.

The Apache 2.0 license remains in effect for all other recipients.
The CC-BY 4.0 grant is non-conflicting with Apache 2.0 (both permit
commercial use; neither imposes share-alike on derivatives).

## What is NOT relicensed (Kaggle Rules §2.5.a.3 carve-out)

> "In the event that input data or pretrained models with an
> incompatible license are used to generate your winning solution,
> you do not need to grant an open source license in the preceding
> Section for that data and/or model(s)."

The following components remain under their upstream licenses and
are **not** subject to this CC-BY 4.0 grant:

| Component | Upstream license | Source |
|---|---|---|
| Gemma 4 weights | Apache 2.0 (unchanged) | https://ai.google.dev/gemma/apache_2 |
| TRIBE v2 weights | CC-BY-NC 4.0 (NonCommercial) | https://github.com/facebookresearch/tribev2 |
| V-JEPA 2 (encoder inside TRIBE v2) | CC-BY-NC 4.0 | Meta Platforms, Inc. |
| wav2vec-BERT 2.0 (encoder inside TRIBE v2) | MIT | Meta Platforms, Inc. |
| Llama 3.2 3B (encoder inside TRIBE v2) | Llama 3.2 Community License | Meta Platforms, Inc. |
| Schaefer 400 cortical atlas | CC-BY 4.0 | Yeo Lab |

The TRIBE v2 NonCommercial restriction means **commercial deployments
of Cortex require a separate license from Meta Platforms, Inc.** This
applies to anyone running the Cortex pipeline against TRIBE v2
weights, including users who fork this repository.

## Attribution

Required attribution if the CC-BY-4.0-licensed Cortex code is used:

> "Cortex by Alexios Bluff Mara LLC (Red Team Kitchen). Licensed under
> CC-BY 4.0 for Gemma 4 Good Hackathon winning purposes; otherwise
> Apache 2.0. Built on Gemma 4 (Apache 2.0, Google LLC) and TRIBE v2
> (CC-BY-NC 4.0, Meta Platforms, Inc.)."

## Authority

The undersigned represents that they have unrestricted right to grant
this license per Kaggle Competition-Specific Rules §2.8.b.

— Soumit Lahiri, sole member, Alexios Bluff Mara LLC
  Date: (signed at submission)
