"""P3's engine layer (ARCHITECTURE.md §3, §10-12) — orchestrator, clocks,
live/replay loops, position monitor. Only `event_log.py` exists so far
(Principle 2: the event log is the truth) — everything else lands with
the strategy/risk/execution subsystems that consume it.
"""
