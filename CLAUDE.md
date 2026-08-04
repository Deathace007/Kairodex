# Kairodex

AI-assisted options-buying paper trading research platform (NSE + US markets).

**Read `docs/PROGRESS.md` first, every session.** It has current phase status,
exact commands, VM/SSH details, and known gotchas. Deeper reasoning lives in
`docs/SPEC_REVIEW.md` (spec critique + decisions), `docs/ARCHITECTURE.md`
(system design), and `docs/adr/` (why specific choices were made).

**Critical workflow rule:** this repo edits locally but runs on a remote VM.
Never run `docker compose up`, start Postgres/Redis, or execute long-lived
ingestion/test commands against real data on the local machine — SSH to the
VM (details in `docs/PROGRESS.md` §1) instead. Local is for writing code,
linting, type-checking, and the DB-free unit tests only.
