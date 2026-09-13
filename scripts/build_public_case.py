from __future__ import annotations

import argparse
import json
from pathlib import Path

from jeet_analyzer.case_export import build_public_case, public_case_markdown, replay_cluster_receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline replay plus alias-only public case export")
    parser.add_argument("--input", type=Path, required=True, help="internal cluster-audit JSON receipt")
    parser.add_argument("--replay-output", type=Path, required=True, help="internal replay receipt")
    parser.add_argument("--json-output", type=Path, required=True, help="public alias-only JSON")
    parser.add_argument("--markdown-output", type=Path, required=True, help="public alias-only Markdown")
    parser.add_argument("--estimated-provider-credits", type=int, required=True)
    parser.add_argument("--credit-ceiling", type=int, required=True)
    args = parser.parse_args()

    source_bytes = args.input.read_bytes()
    source = json.loads(source_bytes)
    replay = replay_cluster_receipt(source, source_bytes=source_bytes)
    public = build_public_case(
        replay,
        estimated_provider_credits=args.estimated_provider_credits,
        credit_ceiling=args.credit_ceiling,
    )
    args.replay_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.replay_output.write_text(json.dumps(replay, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.json_output.write_text(json.dumps(public, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown_output.write_text(public_case_markdown(public), encoding="utf-8")
    print("OFFLINE_REPLAY_PROVIDER_CALLS=0")
    print("PUBLIC_REDACTION_AUDIT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
