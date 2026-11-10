"""A only. Default: authored fixture; --live: explicit paid API."""
from wemeet_ai.m1.a_coaching import CoachRequest, coach_reply
from wemeet_ai.m0 import Runtime
from _data import sample
from _fixture import FixtureClient
from _run import options, show_result

if __name__ == "__main__":
    args = options()
    payload = sample('A')  # See requests/A.json; SW may replace with its own data.
    request = CoachRequest.model_validate(payload)
    runtime = Runtime() if args.live else Runtime(FixtureClient())
    result = coach_reply(request, runtime=runtime)
    show_result('A', payload, result, args)
