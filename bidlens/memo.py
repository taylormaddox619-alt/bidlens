"""Draft award recommendation memo, built only from buyer-approved data."""

import json
import time

import anthropic

from . import config
from .extract import cost_usd, load_prompt
from .rules import label
from .schemas import RFQ


def memo_payload(rfq: RFQ, ranked: list[dict]) -> dict:
    """`ranked`: rows ordered best-first with supplier, landed, scores, values, flags."""
    return {
        "rfq": rfq.model_dump(mode="json"),
        "bids": [
            {
                "rank": i + 1,
                "supplier": r["supplier"],
                "overall_score": round(r["overall"], 1),
                "landed_total_usd": round(r["landed"].landed_total_usd, 2),
                "landed_per_unit_usd": round(r["landed"].landed_per_unit_usd, 2),
                "quoted_unit_price": f"{r['landed'].unit_price_quoted:,.2f} {r['landed'].currency}",
                "duty_usd": round(r["landed"].duty_usd, 2),
                "freight_usd": round(r["landed"].freight_usd, 2),
                "freight_estimated": r["landed"].freight_estimated,
                "lead_time_weeks": r["values"].get("lead_time_weeks"),
                "payment_terms": r["values"].get("payment_terms"),
                "warranty_months": r["values"].get("warranty_months"),
                "open_flags": [f"{f['severity']}: {f['message']}" for f in r["flags"]],
            }
            for i, r in enumerate(ranked)
        ],
    }


def template_memo(rfq: RFQ, ranked: list[dict], weights: dict) -> str:
    best = ranked[0]
    naive = min(ranked, key=lambda r: r["landed"].unit_price_usd)
    lines = [
        f"#### Award Recommendation - {rfq.event_name}",
        "",
        f"**Summary.** Recommend **{best['supplier']}** for {rfq.quantity:,} units of {rfq.item}. "
        f"Estimated landed cost **${best['landed'].landed_total_usd:,.0f}** "
        f"(${best['landed'].landed_per_unit_usd:,.2f}/unit), overall score {best['overall']:.1f}/100.",
        "",
    ]
    if naive["supplier"] != best["supplier"]:
        delta = naive["landed"].landed_total_usd - best["landed"].landed_total_usd
        lines += [
            f"The lowest quoted unit price came from {naive['supplier']} "
            f"(${naive['landed'].unit_price_usd:,.2f}/unit USD), but after freight, duty, tooling, and payment terms "
            f"its landed cost is ${naive['landed'].landed_total_usd:,.0f} - "
            f"**${delta:,.0f} more** than the recommendation.",
            "",
        ]
    lines += [
        "##### Evaluation",
        f"Weights: cost {weights['cost']}, lead time {weights['lead_time']}, terms {weights['terms']}, "
        f"risk {weights['risk']}.",
        "",
        "| Rank | Supplier | Landed / unit | Lead time | Terms | Score |",
        "|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(ranked, 1):
        lt = r["values"].get("lead_time_weeks")
        lines.append(
            f"| {i} | {r['supplier']} | ${r['landed'].landed_per_unit_usd:,.2f} | "
            f"{f'{lt:g} wks' if lt else 'n/a'} | {r['values'].get('payment_terms') or 'n/a'} | {r['overall']:.1f} |"
        )
    lines += ["", "##### Risks and Required Follow-ups"]
    if best["flags"]:
        for f in best["flags"]:
            lines.append(f"- **{label(f['field'])}** ({f['severity']}): {f['message']}")
    else:
        lines.append("- No open exceptions on the recommended supplier.")
    lines += ["", "##### Recommendation",
              f"Proceed with {best['supplier']} subject to closing the follow-ups above."]
    return "\n".join(lines)


def llm_memo(rfq: RFQ, ranked: list[dict], weights: dict, api_key: str,
             model: str = config.DEFAULT_MODEL) -> tuple[str | None, dict]:
    client = anthropic.Anthropic(api_key=api_key)
    payload = memo_payload(rfq, ranked)
    payload["weights"] = weights
    meta = {"source": "live", "model": model, "prompt_version": config.MEMO_PROMPT_VERSION}
    start = time.perf_counter()
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=4000,
            system=load_prompt(config.MEMO_PROMPT_VERSION),
            messages=[{"role": "user", "content": "Draft the award memo from this verified data:\n\n"
                                                  f"```json\n{json.dumps(payload, indent=2)}\n```"}],
            betas=[config.FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": "medium"},
        )
    except anthropic.APIError as e:
        meta.update(status="failed", error=str(e), latency_s=time.perf_counter() - start)
        return None, meta

    meta.update(
        model=response.model, input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cost_usd=cost_usd(response.model, response.usage.input_tokens, response.usage.output_tokens),
        latency_s=time.perf_counter() - start, request_id=response._request_id,
    )
    if response.stop_reason == "refusal":
        meta.update(status="failed", error="Model declined")
        return None, meta
    text = "".join(b.text for b in response.content if b.type == "text")
    meta["status"] = "ok"
    return text, meta
