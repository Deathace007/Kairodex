"""ARCHITECTURE.md §15's WS message shape + Redis pub/sub — a small,
neutral package rather than living in `kairodex.engine` (which publishes)
or `kairodex.api` (which subscribes/fans out): `kairodex.api` may not
import `kairodex.engine` at all (import-linter's "API is glue" contract),
so the message shape both sides agree on has to live somewhere neither
of them owns.
"""
