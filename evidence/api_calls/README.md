# Sponsor API call receipts

These JSONL files contain sanitized receipts from actual integration runs, including
failures and timeouts. A receipt is a local record, not independent proof of success;
use query/session IDs to cross-check provider dashboards where available. Chalk cache
hits do not create a new API-call receipt. Grok call IDs are local correlation IDs.

Every subsequent Grok, Chalk or Factory log event automatically appends a receipt
here as well as the full private log under `logs/`. Restart already-running sponsor
processes after installing this change. Files update locally after calls; publishing
new receipts to GitHub requires a commit and push. Network/Git operations never run
inside the gaze, blink or arm interaction.

Published fields: timestamp, correlation/session IDs, latency, result/error category,
API-attempt and acceptance flags, development/demo mode. No credentials, audio,
transcripts, prompts, user IDs, habit probabilities, source data, absolute paths or
Factory response bodies are published. Unknown categorical values become `other`.
An `accepted: false` demo receipt does not imply that a physical arm was commanded.
Older records can lack fields added later.

To regenerate from existing private logs, stop sponsor processes first, then run:

```sh
python -m sponsors.call_evidence
git add evidence/api_calls
git commit -m "Update sponsor API call receipts"
git push origin main
```
