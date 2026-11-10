"""E.plan only. Default: authored fixture; --live: explicit paid API."""
from wemeet_ai.m1.e_member_plan import MemberPlanRequest, generate_member_plan
from wemeet_ai.m0 import Runtime
from _data import sample
from _fixture import FixtureClient
from _run import options, show_result

if __name__ == "__main__":
    args = options()
    payload = sample('E.plan')  # See requests/E.plan.json; SW may replace with its own data.
    request = MemberPlanRequest.model_validate(payload)
    runtime = Runtime() if args.live else Runtime(FixtureClient())
    result = generate_member_plan(request, runtime=runtime)
    show_result('E.plan', payload, result, args)
