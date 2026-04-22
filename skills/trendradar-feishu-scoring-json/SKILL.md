---
name: trendradar-feishu-scoring-json
description: Update TrendRadar's finance Feishu digest scoring JSON when the user wants to tune which macro, A-share, Hong Kong, US, commodity, or source signals rank higher in the pushed top10 card.
---

# TrendRadar Feishu Scoring JSON

Use this skill when the task is to adjust TrendRadar's finance digest ranking logic without changing Python code.

Target file:

- `config/feishu_digest_scoring.json`

Rules:

- Edit only the JSON file unless the user explicitly asks for code changes.
- Keep the file valid JSON. Do not add comments or trailing commas.
- Preserve keys the user did not ask to change.
- After editing, explain how the change will affect top10 selection.

Main knobs:

- `group_weights`: raise or lower whole topic groups such as `宏观政策`, `大宗商品`, `中港股市`, `美股与波动率`
- `source_weights`: rebalance official sources, premium media, and social sources
- `market_priority_keywords`: add keywords that should get extra priority
- `low_signal_patterns`: titles matching these patterns are treated as low signal
- `low_signal_penalty_per_match`: strengthen or weaken low-signal suppression
- `minimum_score`: drop anything below this line so the card can contain fewer than 10 items

Working method:

1. Read `config/feishu_digest_scoring.json`.
2. Apply the smallest change that matches the user's intent.
3. If asked to favor a market, prefer changing `group_weights` and `market_priority_keywords`.
4. If asked to suppress noisy English listicles or preview pieces, prefer changing `low_signal_patterns` and `low_signal_penalty_per_match`.
5. Report which keys changed and what should rank differently on the next scheduled run.

Notes:

- The new scoring takes effect on the next `uv run python -m trendradar`.
- This skill changes ranking only. It does not change crawl sources, schedule windows, or dedupe history.
