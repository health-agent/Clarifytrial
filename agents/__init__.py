"""The 10 LLM agents of ClarifyTrial Agent v1.2-final.

All agents read from and write to the central shared state
(``models.PatientSession``) owned by the Eligibility State Tracker.
These modules expose typed contracts and several deterministic fallbacks.
There are no real LLM calls or external APIs yet.
"""
