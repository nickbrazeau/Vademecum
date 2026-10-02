"""What the MCP tests share: paths, scripted content, and two call helpers.

A module with a name of its own, imported by the tests explicitly, because
``conftest`` is the name the API's tests import theirs by and the two suites
run from the same root.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
API_TESTS = REPO_ROOT / "apps" / "api" / "tests"
if str(API_TESTS) not in sys.path:
    # For the API suite's scripted fakes (fake_model, fake_appserver), which
    # are what make a build and a grade run with no model call.
    sys.path.insert(0, str(API_TESTS))

from mcp import Client  # noqa: E402

API_ORIGIN = "http://127.0.0.1:8765"
MCP_ORIGIN = "http://127.0.0.1:8766"
PASSPHRASE = "correct horse battery staple"

# --- scripted content, the same shapes the API's end-to-end test uses -------

LECTURE = (
    "Septic shock: resuscitation targets.\n\n"
    "Serial lactate measurement guides resuscitation in septic shock. "
    "A fall of at least ten per cent within two hours is associated with "
    "improved survival in the cohorts reviewed here. Noradrenaline remains "
    "the first-line vasopressor for adults with septic shock.\n"
)
QUOTE = "Serial lactate measurement guides resuscitation in septic shock"
ABSTRACT = (
    "We studied resuscitation targets in septic shock across two centres. "
    "Serial lactate measurement guides resuscitation in septic shock and "
    "predicted mortality in this cohort of 412 adults admitted to intensive "
    "care with a vasopressor requirement, followed to ninety days."
)
SYNTHESIS = {
    "points": [
        {
            "claim": "Lactate clearance guides resuscitation in septic shock",
            "detail": "From the uploaded lecture.",
            "topics": ["septic shock"],
            "citations": [{"excerpt_id": "E1", "quote": QUOTE}],
            "unclear": False,
            "questions": [
                {
                    "prompt": "How is resuscitation guided in septic shock?",
                    "reference_answer": "By serial lactate measurement.",
                    "rubric": "Mentions serial lactate measurement.",
                    "citations": [{"excerpt_id": "E1", "quote": QUOTE}],
                }
            ],
        }
    ],
    "search_topics": ["septic shock"],
}
EVIDENCE = {"relation": "supports", "quote": QUOTE, "reasoning": "The abstract states it."}
ASSESSMENT = {"verdict": "sound", "problems": [], "notes": ""}
GRADE = {
    "outcome": "partially_correct",
    "feedback": "You named the measurement but not the interval.",
    "strengths": "Correctly identified serial lactate.",
    "missing_or_unsafe": "No mention of the two-hour window.",
    "improved_answer": "Serial lactate measurement, reassessed at two hours.",
    "uncertainty": "",
}


async def call(client: Client, name: str, arguments: dict | None = None) -> dict:
    """Call a tool and return its structured content; fail on a tool error."""
    result = await client.call_tool(name, arguments or {})
    assert not result.is_error, [getattr(block, "text", "") for block in result.content]
    assert result.structured_content is not None, name
    return result.structured_content


async def call_expecting_error(client: Client, name: str, arguments: dict | None = None) -> str:
    result = await client.call_tool(name, arguments or {})
    assert result.is_error, result.structured_content
    return "\n".join(getattr(block, "text", "") for block in result.content)
