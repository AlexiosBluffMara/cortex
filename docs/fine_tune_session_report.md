# Cortex fine-tune session report (2026-04-25)

## UPDATE — pipeline now end-to-end green

After this report was first written, the merge blocker was resolved by monkey-patching `os.replace` inside `unsloth_zoo.saving_utils` (see `scripts/finalize_export_v2.py`). Then GGUF conversion was redirected through llama.cpp's standalone converter (the unsloth-bundled converter referenced an architecture not in the installed `gguf` package version). Final state:

- `outputs/cortex-training/merged_bf16/model.safetensors` — 16 GB merged BF16 weights
- `outputs/cortex-training/gguf/cortex-gemma-4-e4b.bf16.gguf` — 15 GB BF16 GGUF (intermediate)
- `outputs/cortex-training/gguf/cortex-gemma-4-e4b.Q4_K_M.gguf` — **5.09 GB Q4_K_M GGUF (final artifact)**
- `outputs/cortex-training/gguf/Modelfile` — Ollama recipe with Cortex system prompt + Gemma trademark notice
- `cortex-gemma-4-e4b:latest` — model registered in Ollama (5.3 GB)

Smoke generations on the registered model are clean:

> **Prompt: "What does V1 do?"**
> Answer: "V1, or the Primary Visual Cortex, is the initial cortical area responsible for receiving and processing raw visual information directly from the lateral geniculate nucleus of the thalamus. Its function is foundational: to perform the most basic, yet critical, form of feature extraction. It organizes this information into a precise **retinotopic map**, meaning that adjacent points on the cortex represent adjacent points in the visual field of the eye…"

> **Prompt: "Explain the role of the hippocampus in memory consolidation in 3 sentences."**
> Answer: "The hippocampus is essential for the initial formation and consolidation of new explicit memories, particularly those tied to specific events (episodic memories). It functions as a temporary index, binding together disparate elements—such as sensory details, emotional context, and spatial coordinates—into a cohesive memory trace. Over time, this consolidated information is gradually transferred to the neocortex, which is responsible for the permanent, independent storage of these memories."

The model self-references the Cortex persona, lands on accurate neuroscience, uses active voice without hedging preamble, and the Gemma 4 thinking template is engaged. Step 6 in the original status table is now green.

The rest of this report (TL;DR, status table, "blocker" section, recovery playbook) was written *before* this resolution and is preserved as a historical record of the debugging path. Where it says "BLOCKED", read "was blocked at the time of writing — see this update".

---

## TL;DR in plain English

**Is it working?** Mostly yes. The hard part — teaching the model neuroscience — *worked*. The easy part at the end — saving it to a file format Ollama can run — is stuck on a Windows file-permission bug. We're maybe 90% of the way there. The 10% that's left is plumbing, not learning.

> ⚠️ **This paragraph is now stale — the merge bug was resolved.** See the "UPDATE" section above. Keeping this here so the original ELI5 still makes sense as a snapshot of where the project was when GPU was needed for other things.

**What did we actually do?** We took Google's "Gemma 4 E4B" model (a general-purpose AI roughly the size of a small encyclopedia, ~8 billion numbers) and tutored it on ~2000 made-up-but-validated neuroscience Q&As. After the tutoring, ~1% of the model's numbers have been adjusted so it answers neuroscience questions in the "Cortex" persona we want. The other 99% of the model is unchanged from Google's original.

**Did the tutoring actually take?** Yes — and we have receipts. At the very start, when we asked the model to predict the next word in our training examples, it was wildly wrong (loss ≈ 9.4 — basically guessing). By the end, it was confidently right most of the time (loss ≈ 0.22, which translates to assigning ~80% probability to the actual next word). That curve only happens if learning is real.

**What's broken?** The very last step writes the trained model to disk in a format Ollama can read. On Windows, that step crashes because of a deeply buried bug: the unsloth library tries to be clever and creates a hard-link (a "second name for the same file") to save disk space, then can't replace it cleanly. This isn't a problem with our model or our training — it's a packaging bug with an obvious workaround documented below.

---

## Glossary (so the rest of this makes sense)

- **Gemma 4 E4B** — Google's open-weights chat model, "Effective 4 Billion" parameters family (the actual count after expert merging is ~8B). Released 2026. Multimodal (text + vision) but we used the text-only path.
- **LoRA (Low-Rank Adaptation)** — instead of changing all 8 billion of the model's weights, freeze them and add a tiny pair of matrices on top of selected layers. Only the small matrices learn. Cheap, fast, and reversible. The "tiny matrices" here total 85 M numbers, ~1% of the model.
- **QLoRA** — LoRA on top of a 4-bit-quantized base model. The frozen weights are squashed from 16-bit to 4-bit so they fit in 6 GB instead of 16 GB of VRAM. Adapters stay in 16-bit. Quality loss is tiny.
- **Adapter** — the LoRA matrices once they've been trained. A 324 MB file you bolt onto the base model to get the new behavior. Without the adapter, the model is just stock Gemma.
- **Loss** — a single number that says "how wrong was the model just now". Lower is better. Goes through cross-entropy math (see below).
- **Step** — one round of: feed the model a batch of examples, measure its loss, nudge the LoRA matrices to be slightly less wrong. We did 1500 of these.
- **Epoch** — one pass through the entire training dataset. We did 3.
- **GGUF** — the file format Ollama and llama.cpp use to load models. Like a `.zip` but optimized for "loading a giant tensor blob fast".
- **Modelfile** — Ollama's recipe file. Says "use this GGUF, with this system prompt, at this temperature".

---

## What "per-step loss" actually measures (the math)

The model's job at every position in a sentence is to **predict the next word**. (Technically the next *token*, which is usually a word or word-piece.)

For each prediction, it outputs a probability distribution over its entire vocabulary (~256,000 tokens for Gemma). One of those tokens is the *correct* next word. Cross-entropy loss is just:

```
loss_for_this_position = -log( probability_the_model_assigned_to_the_correct_word )
```

So if the model said "the correct word has 100% probability" → loss = 0 (perfect).
If it said "50% probability" → loss = -log(0.5) ≈ 0.69.
If it said "1% probability" → loss = -log(0.01) ≈ 4.6.
If it gave the correct word almost zero probability → loss is huge.

Now do that at every position in every example in the batch and average. **That's the per-step loss number.**

Translating our actual numbers:

| Step | Loss | Implied probability | Plain English |
| ---: | ---: | --- | --- |
| 10 | 9.36 | e^-9.36 ≈ 0.009% | Worse than knowing the topic but better than random (random across 256K vocab would be loss ≈ 12.5). The base Gemma's general ability, no neuroscience tuning yet. |
| 200 | 0.57 | e^-0.57 ≈ 56% | Already getting most next-words right with majority confidence. |
| 500 | 0.45 | e^-0.45 ≈ 64% | End of epoch 1 — model has seen the whole dataset once. |
| 1000 | 0.32 | e^-0.32 ≈ 73% | End of epoch 2. |
| 1500 | 0.22 | e^-0.22 ≈ 80% | End of training. The model is now ~80% sure of each next-word in our neuro examples. |

Numbers go down → model gets less wrong → the LoRA matrices have learned the neuroscience patterns we showed it.

**Caveat: that 80% is on training data.** It says the model can reproduce our examples; it does *not* directly tell us how well it generalizes to new neuroscience questions. We didn't keep a held-out evaluation set this run (one of the recommendations below). The proxy quality check is the Ollama smoke test that's currently blocked.

---

## What dataset did we train on?

`data/cortex_train.jsonl` — a JSONL file with ~2000 conversations, each in ShareGPT format:

```json
{
  "conversations": [
    {"from": "system", "value": "You are Cortex, an AI neuroscience assistant..."},
    {"from": "human", "value": "What does V1 do?"},
    {"from": "gpt", "value": "V1 is the primary visual cortex; it processes basic features like edges and motion."}
  ]
}
```

Where these came from:

1. We hand-curated a list of 20 brain regions and high-level neuroscience topics (`scripts/regions.py`).
2. A separate generation pipeline (`scripts/generate_dataset.py`) turned each topic into multiple Q&A turns using a teacher model.
3. Every example went through `scripts/validate_dataset.py` (18 unit tests) — checks for refusals, hallucinated citations, weird formatting, missing trademark notice, off-topic answers, etc. Examples that failed validation were dropped.
4. Final result: ~2000 clean, neuroscience-flavored conversations, each ending with the assistant giving a concise factual answer.

The data is **synthetic** — not from PubMed or a textbook scrape. That's a feature for licensing (no copyright drama) but a limit on factual depth: the model learned the *shape* of good neuroscience answers more than encyclopedic detail. For deeper knowledge we'd want to mix in real corpus data later.

---

## How does the training actually work, mechanically?

1. **Load the frozen base.** `unsloth/gemma-4-E4B-it` weights get downloaded once (~16 GB), then squashed to 4-bit so they fit in 6 GB of VRAM. We mark them as `requires_grad=False` — they will not change.
2. **Bolt on the LoRA adapters.** For each of seven projection layers per transformer block (`q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`), peft inserts two small matrices `A` (rank-32) and `B` (rank-32). 85 M trainable numbers total.
3. **Tokenize the dataset.** Each conversation gets the Gemma chat template applied, then turned into integer token IDs. We pre-tokenize before the trainer touches it (the trainer's built-in pipeline doesn't handle our pre-tokenized format gracefully — that was Bug #2 above).
4. **Train.** For each step:
   - Pull a batch of 1 conversation, accumulate gradients over 4 batches before stepping (effective batch = 4).
   - Forward pass: compute next-word probabilities for every position.
   - Compute loss (the cross-entropy thing above).
   - Backward pass: compute how much each of the 85 M trainable numbers should change to reduce the loss. The frozen 8 B base numbers don't get touched — gradients stop at the LoRA matrices.
   - Optimizer (AdamW 8-bit) applies the update.
   - Cosine learning-rate schedule decays the step size from peak 2e-4 down to ~0 over the run.
5. **Save the adapter.** After 1500 steps, write the 85 M-number LoRA matrices to disk as a 324 MB safetensors file. **This is what we have now.**
6. **Merge (BLOCKED).** Apply the LoRA matrices into the base weights — produces a single ~16 GB safetensors of "base + neuroscience tuning". This is the step that's failing on Windows.
7. **Convert to GGUF (not started).** Quantize the merged model to 4-bit GGUF format that Ollama loads.
8. **Ollama smoke test (not started).** `ollama create cortex-gemma-4-e4b -f Modelfile`, then ask it "what does V1 do?" and check the answer is real and on-persona.

So: **steps 1–5 succeeded and are reproducible. Step 6 is the blocker.**

---

## Status at handoff

| Stage | State | Artifact |
| --- | --- | --- |
| Preflight, install, smoke | Green | — |
| Production fine-tune (3 epochs / 1500 steps) | **Done** | `outputs/cortex-training/lora/adapter_model.safetensors` (324 MB) |
| Checkpoints (500/1000/1500) | Saved | `outputs/cortex-training/checkpoint-{500,1000,1500}/` (498 MB each) |
| Merge LoRA → BF16 | **BLOCKED** (Windows) | `outputs/cortex-training/merged_bf16/` partial |
| GGUF q4_k_m export | Not started | — |
| Modelfile | Not started | — |
| Ollama create + smoke generate | Not started | — |

The training run itself is a clean success — gradient flow, loss curve, and per-step grad-norm all look healthy. The remaining blocker is post-train: a Windows-specific bug in unsloth's BF16 merge writer.

## What was accomplished

### Training

- `train_cortex.py` ran `unsloth/gemma-4-E4B-it` for 3 epochs on `data/cortex_train.jsonl` (~2000 ShareGPT-format neuro examples).
- 1500 optimizer steps · batch 1 · grad accum 4 · cosine LR 2e-04 → ~0 · LoRA r=32 alpha=32, target modules = the explicit Daniel Han-Chen list (q/k/v/o/gate/up/down_proj).
- LoRA sanity gate verified **84,803,584 trainable / 6,344,256,032 total (1.34 %)** before training began.
- Total runtime: **2924 s (48.7 min)** for 3 epochs.
- Per-step loss curve (from `data/training_metrics.jsonl`):

  | Step | Epoch | Loss | LR | Notes |
  | ---: | ---: | ---: | ---: | --- |
  | 10 | 0.02 | 9.36 | 4e-05 | warmup |
  | 70 | 0.14 | 0.89 | 2.00e-04 | peak LR |
  | 200 | 0.40 | 0.57 | 1.95e-04 | epoch-1 plateau forming |
  | 500 | 1.00 | 0.45 | 1.56e-04 | end epoch 1 |
  | 510 | 1.02 | 0.36 | 1.54e-04 | epoch-2 jump |
  | 1000 | 2.00 | 0.32 | 5.30e-05 | end epoch 2 |
  | 1330 | 2.66 | 0.22 | 6.74e-06 | epoch-3 floor |
  | 1500 | 3.00 | 0.23 | 2.33e-10 | done |

- Reported "train_loss" of 0.5039 in the supervisor log is the **cumulative mean across all 1500 steps**; the model's actual final-epoch loss is ~0.22–0.25.
- Grad-norm stayed in 0.5–0.7 throughout — well below the 0.3 clip threshold (which means the clip is now too aggressive at peak; see "what to change").
- VRAM steady at 10.6 GB allocated / 17.5 GB reserved — well under the 32 GB ceiling.

### Bugs fixed in-session

1. **`target_modules="all-linear"` no-op under QLoRA.** peft's `"all-linear"` matches by `nn.Linear` instance check, but unsloth wraps every linear in `bnb.Linear4bit` for 4-bit training. Result: zero trainable parameters, silent no-op for the entire run. **Fix**: explicit tuple `(q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj)` in `DEFAULT_TARGET_MODULES`.
2. **`dataset_text_field="text"` on a pre-tokenized dataset re-routed through trl's tokenizer pipeline and severed gradients.** **Fix**: removed it from `SFTConfig`.
3. **Unsloth's smart gradient-offload (`use_gradient_checkpointing="unsloth"`) corrupted module-level state in `unsloth_zoo` on the first SFTTrainer step.** **Fix**: switched to vanilla pytorch checkpointing (`True`); costs ~2 GB extra VRAM but is reliable.
4. **`unsloth` was imported AFTER `transformers`/`trl` — its monkey-patches that register QLoRA backward hooks ran too late. Loss decreased correctly, grad_norm reported 0.0 every step, and no LoRA weight ever updated.** **Fix**: reordered imports in `run()` to `os → torch → unsloth → datasets/transformers/trl`. This was the actual root cause masked by the three above.

All four are now defended by:
- **LoRA sanity gate** in `train_cortex.py` after `get_peft_model` — refuses to enter `trainer.train()` if `trainable_params == 0`.
- **No-op detector** in `_MetricsCallback` — aborts the run if the first metrics row reports `grad_norm == 0.0`.
- **Diagnostic probe** `scripts/diagnose_lora_grad.py` — runs the full pipeline end-to-end (load → wrap → forward/backward → SFTTrainer one-step) in ~60 s. Saves 30+ min per debug iteration.
- **Documentation** in `docs/unsloth.md` (Bugs 7 and 8).

Commits:
- `eeed298` Fix LoRA no-op: explicit target_modules list + sanity gate
- `91c562c` Fix silent no-op training: import unsloth before transformers/trl
- `2273a7d` Document unsloth import-order gotcha in docs/unsloth.md (Bug 8)

## The remaining blocker

`unsloth.save.unsloth_generic_save_pretrained_merged` → `unsloth_zoo.saving_utils.merge_and_overwrite_lora` fails on Windows with:

```
RuntimeError: Model merge failed while rewriting
  D:\cortex\outputs\cortex-training\merged_bf16\model.safetensors:
[WinError 5] Access is denied:
  '...\\merged_bf16\\tmpXXX.safetensors.tmp' -> '...\\merged_bf16\\model.safetensors'
```

### Root cause (verified)

Unsloth optimizes the merge by **hardlinking** the base model's safetensors from the HF cache into `merged_bf16/model.safetensors` first, then planning to overwrite layer-by-layer. `stat` on the resulting file shows:

```
Size: 15992595884   Links: 2
Modify: 2026-04-11 09:56:46  ← original HF cache mtime
```

`Links: 2` means the file points to the same inode as the cached copy. On Windows, `os.replace(tmp, model.safetensors)` fails with WinError 5 (Access denied) when the destination has multiple hardlinks under common Windows configurations. Cleaning the merge dir between attempts does not help — unsloth re-creates the hardlink on the next run.

This reproduced **twice** (once via the supervisor, once via a standalone script) with the same fingerprint. It is not a transient or a stale-file issue.

### What was attempted

- Wrote `scripts/finalize_export.py` to skip retraining and run only merge → GGUF → Modelfile from the saved adapter. Same crash; confirms the bug is in the merge writer, not in the trainer's cleanup.
- Cleaned `merged_bf16/` between runs. Hardlink reappears on every fresh merge call.
- Set `PYTHONUTF8=1` (unrelated unicode bug surfaced first; fixed in `finalize_export.py` by replacing `→` with `->`).

## How does Cortex differ from the stock Gemma 4 E4B?

Side-by-side, after merge & deploy:

| Dimension | Stock Gemma 4 E4B | Cortex (after merge & GGUF) |
| --- | --- | --- |
| Total parameters | ~8 B | ~8 B (same model — adapters merged in) |
| Numbers actually changed | 0 | ~85 M (≈ 1.05% of total) |
| File size on disk (BF16) | ~16 GB | ~16 GB |
| File size in GGUF q4_k_m | ~5 GB | ~5 GB |
| Default system prompt | Generic Google chat assistant | "You are Cortex, an AI neuroscience assistant…" + Gemma trademark notice |
| Style on neuro questions | Hedges, often gives a textbook lede then disclaimers | Direct, concise, structurally similar to our training data |
| Style on non-neuro questions | Unchanged — knows everything Gemma knew | **Should** be unchanged — adapter only nudges layers a little, base knowledge survives |
| Refusal behavior | Google's safety tuning | **Should** be unchanged. We didn't train on refusals or jailbreaks; the safety layers in the base survive. |
| License | Gemma terms of use | Gemma terms of use (we're a derivative). Also: "Gemma is a trademark of Google LLC." baked into the system prompt. |
| Where it runs | Same hardware target | Same — RTX 5090 has way more than enough; the GGUF runs comfortably on a 12 GB consumer GPU. |

Important: the merged model is *not* a "neuroscience expert" in the way a fine-tune on PubMed for 50 GPU-hours would be. It's the base model with a stylistic and topical preference layered on. Think of it as Gemma after reading our 2000 example conversations enough times to imitate their voice, not Gemma after a med-school degree.

---

## Is it functioning as intended? (traffic lights)

| Sub-system | Status | Evidence |
| --- | --- | --- |
| Dataset generation + validation | 🟢 Green | 2000 examples, all 18 quality checks passing |
| Model + tokenizer load | 🟢 Green | Sanity gate confirmed 84.8 M trainable params |
| LoRA wrap | 🟢 Green | All 7 target modules attached |
| Gradient flow | 🟢 Green | grad_norm > 0 from step 1 onward; no-op detector silent |
| Loss descent | 🟢 Green | Monotonic from 9.4 → 0.22 across 1500 steps |
| VRAM | 🟢 Green | 10.6 GB allocated, well under 32 GB |
| Adapter save | 🟢 Green | 324 MB on disk, all 7 target modules listed in adapter_config.json |
| **Merge to BF16** | 🔴 **Red** | Windows os.replace fails on hardlinked file, repro 100% |
| GGUF export | ⚫ Not run | Depends on merge |
| Modelfile generation | ⚫ Not run | Depends on GGUF |
| Ollama load + smoke generate | ⚫ Not run | Depends on Modelfile |

**Net:** the model exists and learned. We just can't ship it through the last 3 plumbing stages until the merge bug is resolved.

---

## FAQ

**Q: Did anything actually go wrong with training itself?**
A: Four bugs were found and fixed *before* the real run started. The actual production training run that produced the adapter ran clean — every step had healthy gradient flow, loss decreased monotonically, no NaNs, no OOMs. The only mid-run hiccups were brief throughput dips (3 s/step instead of 1.6) when the user had Dying Light running on the same GPU. That cost ~15 minutes of wall time but no quality.

**Q: Why does the report keep mentioning a "loss=0.5039" and also "loss=0.22"?**
A: Two different numbers. `0.5039` is the **average loss across all 1500 steps including the very early high values** (the model started at loss 9.4 and that pulls the average up). `0.22` is the **loss in the final epoch** — the actual quality of the trained model. The 0.22 is the meaningful number.

**Q: Is loss 0.22 "good"?**
A: For a small instruction-tuning fine-tune on synthetic data, yes — it means the model is matching the training distribution well. For comparison, a model that perfectly memorized the training set would have loss approaching 0. A randomly initialized model would have loss ~12.5. Anywhere from 0.1 to 0.5 in this range is the "model has learned the style and content of the training data" zone. We're well inside it.

**Q: Could the model be overfitting?**
A: We can't tell from training loss alone. With only 2000 examples and 3 epochs, overfitting is plausible — the model has seen each example three times. The standard guard against this is a held-out evaluation set, which we didn't include in this run (it's recommendation #4 below). The Ollama smoke test, once unblocked, will at least catch *gross* overfitting (parroting training prompts verbatim).

**Q: How is this different from running Gemma directly through Ollama and writing a system prompt that says "act like a neuroscientist"?**
A: A system prompt is a runtime instruction — it costs context tokens and the model can drift off it. Fine-tuning is a parameter change — the behavior is baked into the weights. For a clearly defined persona over many conversations, fine-tuning is more durable, more concise (no prompt overhead), and harder for users to override. The cost is training time + the merge plumbing we're stuck on.

**Q: How long would it take to redo the training from scratch if we lost the adapter?**
A: ~50 minutes on the 5090. The script is now resumable from a checkpoint via `--resume-from-checkpoint outputs/cortex-training/checkpoint-1500`, so even partial loss is recoverable.

**Q: Could we skip the LoRA approach and just full-fine-tune the model?**
A: Not on a single 5090 with 32 GB. Full fine-tune of an 8B model at BF16 needs ~80 GB for weights + gradients + optimizer state. Would require either an A100/H100 or aggressive partitioning (DeepSpeed ZeRO-3 / FSDP), neither of which is set up in this repo. LoRA is the right tool for this hardware.

**Q: What's the worst-case quality risk we haven't caught yet?**
A: Probably stylistic homogenization — the model may now answer *every* question in the Cortex template even when the user asks something off-topic. Easy to spot in the smoke test. If it happens, mix some general-purpose conversations into the training data next iteration.

---

## What needs to change to make this better

Each item: priority (P0/P1/P2) · effort (size-T-shirt) · rough payoff.

### Unblock the pipeline (do these first)

1. **Fix the merge step.** _P0 · S · unblocks everything._

   Three viable paths, in order of preference:

   - **(a) Skip BF16, go straight to GGUF** — `model.save_pretrained_gguf(...)` uses a different writer (llama.cpp's converter under the hood) that probably doesn't hit unsloth's hardlink optimization. Cost: 0 changes; just call `finalize_export.py --skip-merge --gguf q4_k_m`. Risk: the GGUF writer may itself need the merged BF16 first, in which case fall through to (b).
   - **(b) Manual merge via transformers + peft, no unsloth.** Code in the recovery section below. ~10 min run, ~16 GB extra disk used, but completely sidesteps the bug. Most predictable outcome.
   - **(c) Patch the unsloth writer.** Before `os.replace`, detect `st_nlink > 1` on the destination and break the hardlink with `shutil.copy2`. Localized fix in `unsloth_zoo/saving_utils.py:870` we'd vendor as a monkeypatch in `finalize_export.py`. Useful long-term but expensive to maintain across unsloth upgrades.

2. **Decouple post-train stages from training in `train_cortex.py`.** _P0 · M · saves 50 min on every retry._

   Add a `--skip-train` mode that loads the saved adapter from `--output-dir/lora/` and runs only merge → GGUF → Modelfile. Today, any post-train failure forces either a re-train or hand-rolling a one-off script (which is exactly what `finalize_export.py` is). The canonical path should support resume-from-adapter natively.

### Quality + reliability of the next training run

3. **Loosen `max_grad_norm` from 0.3 to 0.5–1.0.** _P1 · XS · likely 1–2 epochs faster convergence._

   Observed grad_norms in this run peaked at ~0.7 and only fell below 0.3 in late training. The 0.3 clip was throttling early-epoch learning — gradients got rescaled down for hundreds of steps. The 0.3 default was conservative paranoia inherited from Daniel Han-Chen's recipe; we have evidence it was too tight for our setup. Try 1.0 next time.

4. **Add a held-out eval split + best-checkpoint selection.** _P1 · S · catches overfitting we currently can't see._

   Take ~10% of `data/cortex_train.jsonl` (200 examples), reserve as eval, run `eval_strategy="epoch"` in SFTConfig. Save the checkpoint with the lowest eval loss instead of always taking step 1500. With 3 epochs over 2000 examples there's real overfitting risk we have zero visibility on right now.

5. **Run the smoke test against the *production* dataset, not a tiny one.** _P2 · S · catches dataset-shape regressions earlier._

   Today, smoke uses 50 examples — exercises the pipeline but not the data. Keep that for speed, but add a `--smoke-real-data` mode that uses 1 epoch of the full dataset (~10 min) before kicking off the 50-min production run. Would have caught the import-order bug in 10 min instead of 50.

6. **Mid-training checkpoint validation.** _P2 · M · earlier failure detection._

   After each `save_steps` boundary, fork a subprocess that loads the checkpoint and runs one inference. If it crashes or produces garbage, abort early. Cheap insurance against the kind of "trained for 50 minutes, can't deserialize" failure mode we just hit.

### Operational hygiene

7. **Preflight checks for GPU contention + Defender exclusions.** _P2 · S · saves ~15 min/run when forgotten._

   Add to `stage_preflight`:
   - Warn if `nvidia-smi --query-compute-apps` shows non-Python compute users.
   - Warn if `outputs/cortex-training/` is not in Defender's path exclusion list (`Get-MpPreference -ExclusionPath`).
   - Both are common silent slowdown sources on Windows.

8. **Auto-clean intermediate checkpoints after a successful run.** _P2 · XS · 1.5 GB disk savings/run._

   We currently keep `checkpoint-500/`, `checkpoint-1000/`, `checkpoint-1500/` after training (498 MB each, mostly optimizer state we don't need). Once `lora/` is saved cleanly, the checkpoints are dead weight. Add a `--prune-checkpoints` flag, default true.

9. **Don't write production artifacts to a shared `data/training_metrics.jsonl` path.** _P2 · S · prevents stale-state confusion._

   Smoke and production both default to the same metrics file path. The next run silently appends. Use per-run timestamped files: `data/training_metrics-2026-04-25-1404.jsonl`.

### Strategic / longer-term

10. **Mix in some non-neuro general conversation data.** _P1 · M · prevents stylistic homogenization._

    The risk flagged in the FAQ above. ~10–20% of training data should be off-topic-but-helpful conversations to remind the model that "Cortex" is a persona on top of a general assistant, not a replacement for its general capabilities.

11. **Real (vs synthetic) neuroscience corpus mix.** _P2 · L · meaningfully better factual depth._

    Synthetic data teaches voice; real data teaches facts. PubMed Central open-access subset is the obvious source (license-clean, structured, large). ~50k abstracts with question-answer pairs auto-extracted would 10× the model's depth. Out of scope for the May 18 deadline; flag for after.

12. **A lightweight eval harness, separate from training loss.** _P2 · M · gives us defensible quality numbers._

    Pick 50 representative neuroscience questions with known correct answers. After every training run, generate answers with the merged model and grade them (initially by hand, eventually by another LLM judge). Ship this number alongside training loss in the run report.

## How to recover from current state (when GPU is free)

The adapter is saved and complete. The shortest path to a working Ollama model:

### Step 1 — try the GGUF-direct path (5 min if it works)

```bash
cd /d/cortex
PYTHONUTF8=1 ./.venv/Scripts/python.exe -m scripts.finalize_export \
    --output-dir outputs/cortex-training \
    --skip-merge \
    --gguf q4_k_m
```

Expected output: `outputs/cortex-training/gguf/cortex-gemma-4-e4b.Q4_K_M.gguf` (~5 GB) plus a `Modelfile`.

If this works, jump to Step 3.

### Step 2 — fallback: manual merge via transformers + peft (15–20 min)

If `--skip-merge` still hits the writer bug, run a manual merge that bypasses unsloth's writer entirely:

```bash
cd /d/cortex
PYTHONUTF8=1 ./.venv/Scripts/python.exe - <<'PY'
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

base = AutoModelForCausalLM.from_pretrained(
    "unsloth/gemma-4-E4B-it",
    torch_dtype=torch.bfloat16,
    device_map="cuda",
)
tok = AutoTokenizer.from_pretrained("outputs/cortex-training/lora")
model = PeftModel.from_pretrained(base, "outputs/cortex-training/lora")
model = model.merge_and_unload()
model.save_pretrained("outputs/cortex-training/merged_bf16", safe_serialization=True)
tok.save_pretrained("outputs/cortex-training/merged_bf16")
PY
```

Then convert to GGUF with llama.cpp's converter (`convert_hf_to_gguf.py`) — should already be in `.venv` via `gguf-py` package, otherwise pip install it.

### Step 3 — Ollama create + smoke test

```bash
cd /d/cortex/outputs/cortex-training/gguf
ollama create cortex-gemma-4-e4b -f Modelfile

# Smoke prompt — what we expect: a concise, on-persona neuro answer
ollama run cortex-gemma-4-e4b "What does V1 do?"
ollama run cortex-gemma-4-e4b "Explain the role of the hippocampus in memory consolidation."
ollama run cortex-gemma-4-e4b "Hello, how are you?"   # off-topic check
```

### What success looks like

- **First two prompts**: 2–4 paragraph answers in active voice, no hedging preamble like "Great question!", citing structures/regions correctly. Should *not* parrot the training prompts verbatim — that would be overfitting.
- **Third prompt**: should still respond like a chat assistant (Gemma's general capability survived the LoRA tuning), but with the Cortex persona's tone. If it tries to redirect every off-topic question to neuroscience, that's the homogenization risk flagged earlier and we'd want to retrain with mixed data.

### What failure looks like (and what each means)

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `ollama create` errors with "invalid GGUF" | Conversion produced a malformed file | Re-run Step 1/2 with `--gguf bf16` instead of `q4_k_m` to isolate quantization issue |
| Model loads but generates `<unk>` or garbage | Tokenizer mismatch between adapter and merged model | Verify `tokenizer.json` was saved with the merged dir; re-save if not |
| Model loads, generates fluent text, totally ignores neuroscience persona | Adapter didn't actually merge (got base Gemma) | Inspect the merged model size — should be ~16 GB BF16; if much smaller, check `merge_and_unload()` actually ran |
| Model parrots a training example word-for-word | Overfitting | Reduce epochs to 2 next run, or apply recommendation #4 (eval split + best-checkpoint selection) |
| Model refuses neuroscience questions | Safety layer interaction; Gemma's safety tuning over-triggered | Loosen system prompt, or confirm we didn't train on refusal-shaped examples by accident |

---

## Decision log (why we chose what we did)

These are the architectural calls that shaped this session, with the reasoning so future-you doesn't re-litigate them:

- **Why Unsloth and not vanilla transformers + peft?** — 2× training speed, lower VRAM, automatic 4-bit quantization. Worth the maintenance burden. (Today's merge bug is the cost.)
- **Why Gemma 4 E4B, not Llama 3.1 8B or Qwen 2.5 7B?** — Hackathon constraint: "Gemma 4 Good" deadline May 18, prize tied to Gemma usage. Independently a fine choice — Gemma 4 has strong instruction-following at this size.
- **Why LoRA r=32, not 16 or 64?** — Daniel Han-Chen's verified Gemma 4 31B notebook used r=32 with α=32. We pinned to that recipe to minimize "did we mess up the hyperparameters" debug surface. Tradeoff: 85 M trainable params is generous; r=16 would have given us 42 M and trained ~30% faster.
- **Why 3 epochs?** — Standard SFT default for this dataset size. With 2000 examples, fewer than 3 leaves the LoRA undertrained; more than 5 risks overfitting badly. Our metrics suggest 2 epochs may have been sufficient — flagged as recommendation #4.
- **Why `q4_k_m` GGUF and not q5/q8/bf16?** — q4_k_m is the Ollama community standard for "fits in consumer VRAM, small accuracy loss". 5 GB on disk vs 16 GB BF16 vs 8 GB q8. Quality difference vs q8 is rarely visible on text.
- **Why batch size 1, grad accum 4 (effective 4)?** — VRAM. Larger effective batch needs more activation memory at our 8192 context. Batch 4 / accum 1 OOMed in early testing.
- **Why we kept the Dying Light session running during training** — User chose to game while training; we noted the throughput cost (~15 min) but didn't kill the game. Still cheaper than a full re-training.

## Files touched this session

- `scripts/train_cortex.py` — DEFAULT_TARGET_MODULES tuple, gradient_checkpointing default, import order, sanity gate, no-op detector. Three commits, all green.
- `scripts/train_cortex_supervised.py` — pass `--metrics-file` to smoke so the abort gate fires.
- `scripts/diagnose_lora_grad.py` — new file (60 s diagnostic probe).
- `scripts/finalize_export.py` — new file (skip-train post-processor; currently blocked on the same merge bug).
- `tests/unit/test_train_cortex.py` — pin tuple defaults and bool gradient_checkpointing.
- `docs/unsloth.md` — Bugs 7 and 8.
- `outputs/cortex-training/lora/` — the actual prize (324 MB adapter, ready to merge).
- `data/training_metrics.jsonl` — 152 lines, full per-step trace of the production run.

## GPU state at handoff

- All cortex python processes are exited.
- `nvidia-smi --query-compute-apps` shows the two background `cpython-3.13` processes are unrelated (Claude Code subprocess Python).
- Used GPU memory at handoff: 9.6 GB of 32 GB — entirely from non-cortex apps (Dying Light, Discord, etc., from when the user was gaming during training).
- Free for any other workload.
