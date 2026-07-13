"""
agents.py — ADK Workflow skeleton, Week 1 exit criteria (Phase A, Day 1).

Three stubbed sub-agents wired into a single SequentialAgent workflow:
Discovery -> Reconciliation -> Reporting. No tools yet (Gmail/Drive/Sheets
tools land Tue-Sat this week) — this just proves the ADK orchestration
shape works before any real logic goes in.

Run standalone: python agents.py
"""

import os

from dotenv import load_dotenv
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

load_dotenv()

MODEL = "gemini-2.5-flash"

discovery_agent = LlmAgent(
    name="discovery_agent",
    model=MODEL,
    description="Finds invoices/receipts across Gmail, Drive, and bank statements.",
    instruction=(
        "You are the Discovery Agent for ReconAI. You will eventually search Gmail "
        "and Drive for invoices/receipts and extract structured data from them. "
        "No tools are wired in yet — for now, just acknowledge the reconciliation "
        "request and state that discovery would run next."
    ),
)

reconciliation_agent = LlmAgent(
    name="reconciliation_agent",
    model=MODEL,
    description="Deduplicates, checks for missing invoices, compares against budget.",
    instruction=(
        "You are the Reconciliation Agent for ReconAI. You will eventually dedup "
        "invoices, flag missing ones, compare spend against budget, and tag "
        "category/recurring/GST status. No tools are wired in yet — for now, just "
        "acknowledge the discovery output and state that reconciliation would run next."
    ),
)

reporting_agent = LlmAgent(
    name="reporting_agent",
    model=MODEL,
    description="Builds the monthly reconciliation report and answers NL questions.",
    instruction=(
        "You are the Reporting Agent for ReconAI. You will eventually generate a "
        "monthly reconciliation report and recommendations from the ledger. No "
        "tools are wired in yet — for now, just acknowledge the reconciliation "
        "output and summarize that a report would be produced next."
    ),
)

root_agent = SequentialAgent(
    name="reconai_workflow",
    description="Discovery -> Reconciliation -> Reporting pipeline for ReconAI.",
    sub_agents=[discovery_agent, reconciliation_agent, reporting_agent],
)


def run_once(prompt: str) -> None:
    if not os.getenv("GOOGLE_API_KEY"):
        raise SystemExit("GOOGLE_API_KEY not found. Check your .env file.")

    runner = InMemoryRunner(agent=root_agent, app_name="reconai")
    session = runner.session_service.create_session_sync(
        app_name="reconai", user_id="local-test"
    )

    content = types.Content(role="user", parts=[types.Part(text=prompt)])
    for event in runner.run(
        user_id="local-test", session_id=session.id, new_message=content
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    print(f"[{event.author}] {part.text}")


if __name__ == "__main__":
    run_once("Prepare July reconciliation")
