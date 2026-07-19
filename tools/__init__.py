"""ReconAI tools package.

Each module maps to one agent's toolset, per the architecture doc:
- discovery_tools.py      -> Discovery Agent
- reconciliation_tools.py -> Reconciliation Agent
- reporting_tools.py      -> Reporting Agent
- security.py             -> shared guardrails, wired into all three
- google_auth.py          -> shared OAuth credential loading
"""
