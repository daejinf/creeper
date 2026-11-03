import re
from copy import deepcopy

from .schemas import GoalPlanningRequest


EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?<!\d)(?:01[016789])[- .]?\d{3,4}[- .]?\d{4}(?!\d)")


def redact_text(text: str) -> str:
    text = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)
    return PHONE_PATTERN.sub("[REDACTED_PHONE]", text)


def sanitized_request(request: GoalPlanningRequest) -> GoalPlanningRequest:
    payload = deepcopy(request.model_dump(mode="json"))
    for message in payload["kickoff_messages"]:
        message["text"] = redact_text(message["text"])
    for member in payload["members"]:
        member["constraints"] = [redact_text(value) for value in member["constraints"]]
    for feedback in payload["rejection_feedback"]:
        feedback["reason"] = redact_text(feedback["reason"])
    return GoalPlanningRequest.model_validate(payload)

