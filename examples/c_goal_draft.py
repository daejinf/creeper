"""C only. Default: authored fixture; --live: explicit paid API."""
from wemeet_ai.m1.c_goal_draft import DraftRequest, draft_goal_spec
from wemeet_ai.m0 import Runtime
from _data import sample
from _fixture import FixtureClient
from _run import options, show_result

if __name__ == "__main__":
    args = options()
    payload = sample('C')  # See requests/C.json; SW may replace with its own data.
    request = DraftRequest.model_validate(payload)
    runtime = Runtime() if args.live else Runtime(FixtureClient())
    result = draft_goal_spec(request, runtime=runtime)
    show_result('C', payload, result, args)
