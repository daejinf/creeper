"""B only. Default: authored fixture; --live: explicit paid API."""
from wemeet_ai.m1.b_analysis import AnalysisRequest, analyze_conversation
from wemeet_ai.m0 import Runtime
from _data import sample
from _fixture import FixtureClient
from _run import options, show_result

if __name__ == "__main__":
    args = options()
    payload = sample('B')  # See requests/B.json; SW may replace with its own data.
    request = AnalysisRequest.model_validate(payload)
    runtime = Runtime() if args.live else Runtime(FixtureClient())
    result = analyze_conversation(request, runtime=runtime)
    show_result('B', payload, result, args)
