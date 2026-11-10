"""Public AI entry points. Database and web adapters remain optional."""

from .group import group_coach
from .personal import personal_coach
from .validation import validate_plan
from .shared.contracts import GroupRequest, PersonalRequest, Result, ActorContext

__version__ = "0.3.0"
__all__ = [
    "group_coach",
    "personal_coach",
    "validate_plan",
    "GroupRequest",
    "PersonalRequest",
    "Result",
    "ActorContext",
]
