# Investigation evaluation

Backend: graph=`memory`, llm=`fake`. Alerts: 26 (completed 26). Human review simulated (approve if risk ≥ medium).

| Metric | Value |
|---|---|
| Detection precision / recall / F1 (flag at ≥ medium) | 1.0 / 1.0 / 1.0 (TP 17, FP 0, FN 0) |
| Detection precision / recall / F1 (flag at ≥ high) | 1.0 / 0.824 / 0.903 (TP 14, FP 0, FN 3) |
| Fraud ring discovery precision / recall | 0.718 / 0.267 |
| Retrieval Recall@5 (hybrid) / MRR | 1.0 / 0.95 |
| Evidence completeness | 1.0 |
| Citation correctness (cited evidence matches the section) | 1.0 |
| Unsupported claim rate | 0.0 |
| Latency to review-ready p50 / p95 / max (s) | 0.13 / 0.15 / 0.26 |
| Workflow recovery (injected failures) | 6/6 |

## Per alert

| Alert | Pattern | Fraud? | Score | Category |
|---|---|---|---|---|
| MON-00001 | spec_example | yes | 100 | critical |
| MON-00002 | mule_network | yes | 80 | critical |
| MON-00003 | ato | yes | 60 | high |
| MON-00004 | mule_network | yes | 100 | critical |
| MON-00005 | ato | yes | 65 | high |
| MON-00006 | mule_network | yes | 65 | high |
| MON-00007 | ato | yes | 65 | high |
| MON-00008 | synthetic_identity | yes | 65 | high |
| MON-00009 | synthetic_identity | yes | 65 | high |
| MON-00010 | synthetic_identity | yes | 65 | high |
| MON-00011 | card_testing | yes | 55 | high |
| MON-00012 | card_testing | yes | 55 | high |
| MON-00013 | promo_abuse | yes | 30 | medium |
| MON-00014 | promo_abuse | yes | 30 | medium |
| MON-00015 | promo_abuse | yes | 30 | medium |
| MON-00016 | round_tripping | yes | 75 | critical |
| MON-00017 | round_tripping | yes | 50 | high |
| MON-00018 | household_shared_device | no | 0 | low |
| MON-00019 | household_shared_device | no | 0 | low |
| MON-00020 | household_shared_device | no | 0 | low |
| MON-00021 | shopkeeper_fan_in | no | 10 | low |
| MON-00022 | shopkeeper_fan_in | no | 10 | low |
| MON-00023 | benign_new_phone | no | 0 | low |
| MON-00024 | benign_new_phone | no | 5 | low |
| MON-00025 | ordinary_customer | no | 0 | low |
| MON-00026 | ordinary_customer | no | 5 | low |

> With the fake LLM, citation correctness and unsupported-claim rate measure the pipeline's guardrails, not model quality. Re-run with `LLM_PROVIDER=openai` for model numbers.
