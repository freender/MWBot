---
description: Validate, deploy, verify, commit, push, and reconcile an MWBot change
agent: build
---

validate -> local canary/Worker deploy -> verify -> commit -> push -> CI -> GHCR reconcile

Ship scope [$ARGUMENTS] end to end, inferring whether the change affects the bot, Worker, or
both. Follow AGENTS.md's **Shipping (`/ship`)** section for every step, decide routine details
without questions, preserve unrelated working-tree changes, and report at the end.
