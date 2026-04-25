# Dataset quality report

**Source:** `data\cortex_train.jsonl`
**Total examples:** 124
**Duplicates skipped:** 0

## Examples per region

| Region | Count |
| --- | ---: |
| Primary Visual Cortex | 100 |
| Extrastriate Visual Cortex (V2) | 24 |

## Examples per template family

| Family | Count |
| --- | ---: |
| `activation_meaning` | 40 |
| `stimulus_cause` | 24 |
| `comparison` | 20 |
| `clinical` | 20 |
| `plain_english` | 20 |

## Answer length (words)

- Min: 116
- Median: 232
- Mean: 241
- Max: 409
- Stdev: 67

## Quality flags

| Metric | Count | Rate |
| --- | ---: | ---: |
| Empty answers | 0 | 0.0% |
| Too short (< 50 words) | 0 | 0.0% |
| Too long (> 800 words) | 0 | 0.0% |
| Mentions target region | 124 | 100.0% |
| Mentions Yeo network | 124 | 100.0% |
| Refusal phrases | 47 | 37.9% |

## Verdict

⚠️ Refusal rate is 38% (target ≤2%). Some answers are LLM dodges; review and regenerate flagged examples.
