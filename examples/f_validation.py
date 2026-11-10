"""F only. Default: authored fixture; --live: explicit paid API."""
from wemeet_ai.m1.f_validation import ValidationRequest, validate_plan
from wemeet_ai.m0 import Runtime
from _data import sample
from _fixture import FixtureClient
from _run import options, show_result

if __name__ == "__main__":
    args = options()
    payload = sample('F')  # See requests/F.json; SW may replace with its own data.
    request = ValidationRequest.model_validate(payload)
    runtime = Runtime() if args.live else Runtime(FixtureClient())
    result = validate_plan(request)
    show_result('F', payload, result, args)
