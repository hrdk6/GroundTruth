#!/usr/bin/env python
"""Verify the configured LLM provider before spending a long run on it.

    make llm-check

An evaluation makes hundreds of sequential calls. Discovering on call 200 that
the model id was wrong, or that the endpoint returns prose where the judge
expects JSON, wastes the whole run. This makes three cheap calls and reports
what actually came back.

The strict-JSON check is the one that matters: the judge and the claim
verifier both parse JSON out of the response, and a reasoning model that
narrates before answering will break them. Better to see that here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.llm import LLMClient, MissingAPIKeyError  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.core.settings import get_settings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="Override the model to test")
    parser.add_argument("--no-cache", action="store_true", help="Bypass the LLM cache")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.gt_log_level, settings.gt_log_format)

    print("Provider configuration")
    print(f"  provider        {settings.gt_llm_provider}")
    print(f"  base_url        {settings.gt_llm_base_url or '(provider default)'}")
    print(f"  generation model {settings.gt_generation_model}")
    print(f"  cheap model      {settings.gt_cheap_model}")
    print(f"  key configured   {settings.has_llm_key}")
    if settings.gt_llm_provider != "anthropic":
        print(
            f"  pricing          ${settings.gt_llm_cost_per_mtok_in}/MTok in, "
            f"${settings.gt_llm_cost_per_mtok_out}/MTok out"
        )
        if not (settings.gt_llm_cost_per_mtok_in or settings.gt_llm_cost_per_mtok_out):
            print("                   (zero: fine for a free tier, wrong for a paid one)")
    print()

    if not settings.has_llm_key:
        expected = (
            "ANTHROPIC_API_KEY" if settings.gt_llm_provider == "anthropic" else "GT_LLM_API_KEY"
        )
        print(f"No API key. Set {expected} in .env.", file=sys.stderr)
        return 2

    client = LLMClient()
    use_cache = not args.no_cache
    models = {
        "generation": args.model or settings.gt_generation_model,
        "cheap": args.model or settings.gt_cheap_model,
    }
    failures = 0

    # 1. Can we reach it at all, on each configured model?
    for role, model in models.items():
        print(f"[{role}] {model}")
        try:
            response = client.complete(
                "Reply with exactly: OK",
                model=model,
                max_tokens=32,
                use_cache=use_cache,
            )
        except MissingAPIKeyError as exc:
            print(f"  FAIL  {exc}", file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001 - report whatever the SDK raised
            print(f"  FAIL  {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
            failures += 1
            continue

        preview = " ".join(response.text.split())[:80] or "(empty)"
        print(f"  ok    reply={preview!r}")
        print(
            f"        tokens in={response.usage.input_tokens} "
            f"out={response.usage.output_tokens}  cost=${response.cost_usd:.6f}"
            f"  {response.latency_ms:.0f}ms"
        )
        if not response.text.strip():
            print("  WARN  empty response body — check the model id", file=sys.stderr)
            failures += 1

    # 2. Does the cheap model return parseable JSON? The judge depends on it.
    print(f"\n[strict JSON] {models['cheap']}")
    try:
        response = client.complete(
            'Return strict JSON and nothing else: {"verdict": "supported"}',
            system="You respond with strict JSON only. No prose, no explanation, no markdown.",
            model=models["cheap"],
            max_tokens=128,
            use_cache=use_cache,
        )
        raw = response.text.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            print(f"  FAIL  no JSON object in reply: {raw[:200]!r}", file=sys.stderr)
            failures += 1
        else:
            parsed = json.loads(match.group(0))
            clean = raw.startswith("{") and raw.endswith("}")
            print(f"  ok    parsed={parsed}")
            if not clean:
                print(
                    "  note  the model wrapped its JSON in prose. The judge and "
                    "verifier extract JSON from prose, so this works — but it "
                    "costs tokens and is worth knowing."
                )
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        failures += 1

    stats = client.tracker.to_dict()
    print(f"\n{stats['calls']} call(s), ${stats['cost_usd']:.6f} total")

    if failures:
        print(f"\n{failures} check(s) failed. Fix these before running an eval.", file=sys.stderr)
        return 1

    print("\nProvider looks usable. Next: make eval CONFIG=configs/full.yaml MODE=full")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
