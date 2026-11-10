"""D only. Default: authored fixture; --live: explicit paid API."""
from wemeet_ai.m1.d_group_plan import ConfirmedGoal, generate_group_plan
from wemeet_ai.m0 import Runtime
from _data import sample
from _fixture import FixtureClient
from _run import options, show_result

if __name__ == "__main__":
    args = options()
    payload = sample('D')  # See requests/D.json; SW may replace with its own data.
    request = ConfirmedGoal.model_validate(payload)
    runtime = Runtime() if args.live else Runtime(FixtureClient())
    result = generate_group_plan(request, runtime=runtime)
    show_result('D', payload, result, args)
