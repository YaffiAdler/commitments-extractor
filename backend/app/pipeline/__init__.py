"""Extraction pipeline, one module per stage.

ingest -> preprocess -> segment -> classify -> extract -> normalize -> confidence,
wired together by ``orchestrator``.
"""
