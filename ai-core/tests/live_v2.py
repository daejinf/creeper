"""Explicit anonymous LIVE regression for the originally reported interview loop."""
import json
from pathlib import Path

from wemeet_ai import GoalAI
from wemeet_ai.examples import design_coaching_regression
from wemeet_ai.tracing import MemoryTraceSink


if __name__ == "__main__":
    result = GoalAI(trace_sink=MemoryTraceSink()).coach_reply(design_coaching_regression())
    output = Path("runs/live-v2-coach.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    passed = result.status == "OK" and result.data.kind == "PLAN_PROPOSAL" and result.data.question is None
    print(json.dumps({"passed":passed,"status":result.status,"error":result.error.model_dump() if result.error else None,
                      "run_id":result.meta.run_id,"usage":result.meta.usage.model_dump(),"latency_ms":result.meta.latency_ms},ensure_ascii=False))
    raise SystemExit(0 if passed else 1)
