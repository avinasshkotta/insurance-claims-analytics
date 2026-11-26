---
title: Claims Triage Console
emoji: 🧾
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 8000
pinned: false
license: mit
short_description: Four-layer GenAI claims assistant with a public quality dashboard
---

# Claims Triage Console

A four-layer GenAI insurance claims assistant: LLM summaries, citation-grounded policy
answers that refuse instead of guessing, a triage agent that routes to a human, and three
least-privilege MCP servers.

- **Console:** `/` — triage claims, ask policy questions, watch the adjuster queue
- **Quality & cost dashboard:** `/dashboard` — golden-set pass rate, p95 latency,
  cost per request, human override rate

This Space runs in **demo mode**: requests are rate limited per visitor, uploads are
disabled, and a hard daily spend cap blocks further model calls. Synthetic claim data only.

Source and full README: https://github.com/avinasshkotta/insurance-claims-analytics
