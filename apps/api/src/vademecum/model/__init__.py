"""Model-facing work: what is sent, what is required back, and what is believed.

Nothing here opens a socket or spawns a process. It composes prompts, hands them
to an ``appserver.turns.TurnRunner``, validates what comes back, and writes only
what survived. The privacy claims the interface makes are descriptions of
``prompts.py`` and ``grading.py``; the integrity claims are descriptions of
``build.py`` and ``storage/learning.py``.
"""

from .build import BuildOutcome, BuildRunner
from .grading import (
    GRADING_DISCLOSURE,
    PATIENT_SPECIFIC_NOTICE,
    Disclosure,
    PatientSpecificRequest,
    grade,
    looks_patient_specific,
    self_assess,
    unavailable_reason,
)
from .service import BuildService

__all__ = [
    "BuildOutcome",
    "BuildRunner",
    "BuildService",
    "Disclosure",
    "GRADING_DISCLOSURE",
    "PATIENT_SPECIFIC_NOTICE",
    "PatientSpecificRequest",
    "grade",
    "looks_patient_specific",
    "self_assess",
    "unavailable_reason",
]
