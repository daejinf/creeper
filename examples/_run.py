import argparse
import json
from pathlib import Path

def options():
    parser = argparse.ArgumentParser(description="기본은 작성된 fixture입니다. --live만 실제 유료 API를 호출합니다.")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()

def show_result(module, payload, result, args):
    print("LIVE · 실제 API 실행" if args.live and module != "F" else "CODE · AI 호출 없음" if module == "F" else "REPLAY · 작성된 fixture · AI 호출 없음")
    output = {"module": module, "request": payload, "response": result.model_dump(mode="json")}
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output: args.output.write_text(text, encoding="utf-8")
    print(text)
    raise SystemExit(1 if result.status == "FAILED" else 0)
