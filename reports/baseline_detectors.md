# Detection results

Dataset: 5,198 accounts, 196,921 transactions, 193,084 logins, 13 planted rings (148 actor accounts, 137 victim accounts).

## Graph detectors

| Detector | Findings | Accounts flagged | Precision | Actor recall | Victim recall | Benign FPs | Runtime |
|---|---|---|---|---|---|---|---|
| `shared_identity_clusters` | 8 | 138 | 97% | 82% | 9% | 4 | 0.2s |
| `rapid_pass_through` | 30 | 30 | 97% | 20% | 0% | 1 | 3.9s |
| `value_cycles` | 1 | 4 | 100% | 3% | 0% | 0 | 0.0s |
| `account_takeover` | 12 | 13 | 92% | 0% | 9% | 1 | 9.7s |
| `card_testing` | 4 | 125 | 100% | 0% | 91% | 0 | 0.3s |
| `referral_farms` | 3 | 69 | 100% | 47% | 0% | 0 | 0.1s |
| **all combined** | 58 | **281** | **98%** | **94%** | **100%** | **5** | |

## Ring recall

| Ring | Pattern | Labelled accounts | Flagged | Caught |
|---|---|---|---|---|
| RING-MULE_NETWORK-1 | mule_network | 14 | 12 | ✅ |
| RING-MULE_NETWORK-2 | mule_network | 15 | 14 | ✅ |
| RING-MULE_NETWORK-3 | mule_network | 15 | 13 | ✅ |
| RING-SYNTHETIC_IDENTITY-1 | synthetic_identity | 12 | 11 | ✅ |
| RING-SYNTHETIC_IDENTITY-2 | synthetic_identity | 10 | 9 | ✅ |
| RING-SYNTHETIC_IDENTITY-3 | synthetic_identity | 15 | 14 | ✅ |
| RING-CARD_TESTING-1 | card_testing | 74 | 74 | ✅ |
| RING-CARD_TESTING-2 | card_testing | 51 | 51 | ✅ |
| RING-PROMO_ABUSE-1 | promo_abuse | 21 | 21 | ✅ |
| RING-PROMO_ABUSE-2 | promo_abuse | 24 | 24 | ✅ |
| RING-PROMO_ABUSE-3 | promo_abuse | 24 | 24 | ✅ |
| RING-ROUND_TRIPPING-1 | round_tripping | 6 | 5 | ✅ |
| RING-ROUND_TRIPPING-2 | round_tripping | 4 | 4 | ✅ |

**13/13 rings caught.**

Benign accounts flagged by graph detectors, by trap:

- shared_carrier_or_wifi_ip: 5

## Naive rule baselines (no graph context)

| Rule | Accounts flagged | Precision | Actor recall | Victim recall | Benign FPs | Top FP cause |
|---|---|---|---|---|---|---|
| shared IP (≥5 users) | 5,099 | 5.3% | 91% | 100% | 4,827 | shared_carrier_or_wifi_ip (100%) |
| new device + password reset | 433 | 2.8% | 0% | 9% | 421 | benign_new_phone (100%) |
| high fan-in (≥20 senders) | 63 | 4.8% | 2% | 0% | 60 | shopkeeper_fan_in (100%) |
| high fan-out (≥20 recipients) | 412 | 3.9% | 4% | 7% | 396 | ordinary_customer (94%) |
| any money cycle (≤3 hops) | 4,029 | 5.0% | 43% | 100% | 3,828 | friends_bill_split_cycle (91%) |
