---
paths:
  - "scripts/**/*.py"
  - "tests/**/*.py"
---

# Guarded implementation

Use Python 3.10+ standard library only. Keep validation pure and credentials inside the client helper. Never log keys or provider error bodies. New subscription requests and unverified billing remain blocked. Explicit wallet approval cannot override unknown accounting. Reserve durable intent before POST and never retry it. Save music task IDs before polling and resume through GET only. Keep artifacts private with hashes and untrusted provenance.
