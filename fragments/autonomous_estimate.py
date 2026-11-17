import hashlib
import json

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

from utils.performance import timed


load_dotenv()
client = OpenAI()


AUTONOMOUS_SCHEMA = {
    "type": "object",
    "properties": {
        "currency": {"type": "string", "enum": ["EUR"]},
        "low": {"type": "number"},
        "typical": {"type": "number"},
        "high": {"type": "number"},
        "confidence": {"type": "number"},
        "basis": {"type": "string"},
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "comparables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "seller": {"type": "string"},
                    "description": {"type": "string"},
                    "price_eur": {"type": "number"},
                    "url": {"type": "string"},
                },
                "required": ["seller", "description", "price_eur", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "currency", "low", "typical", "high", "confidence", "basis",
        "assumptions", "comparables",
    ],
    "additionalProperties": False,
}


INSTRUCTIONS = """
You provide an independent market-based price sanity check for custom furniture in Lithuania.
Use web search to find current comparable products, custom-furniture pricing references or supplier
examples. Do not reuse the application's internal catalog prices as market evidence. Return a broad,
honest range, not a fake exact quote. Prefer Lithuanian or nearby EU sources that can serve Lithuania.
Only include comparable offers whose price and exact page URL you actually found. Exclude VAT only
when the source clearly states that. Explain major assumptions such as material, dimensions, hardware,
finish, installation and complexity. This estimate is a comparison only and must not replace the
manufacturer's internal calculation.
"""


def _estimate_hash(payload):
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def autonomous_market_estimate(ai_result, items, project_name, instructions=""):
    compact_parts = []
    for part in (ai_result or {}).get("parts") or []:
        compact_parts.append({
            "name": part.get("name"),
            "material": part.get("material"),
            "component_type": part.get("component_type"),
            "quantity": part.get("quantity"),
            "length_mm": part.get("length_mm"),
            "width_mm": part.get("width_mm"),
            "height_mm": part.get("height_mm"),
            "thickness_mm": part.get("thickness_mm"),
        })

    request = {
        "project_name": project_name,
        "furniture_name": (ai_result or {}).get("furniture_name"),
        "furniture_type": (ai_result or {}).get("furniture_type"),
        "overall_dimensions_mm": {
            "width": (ai_result or {}).get("width_mm"),
            "height": (ai_result or {}).get("height_mm"),
            "depth": (ai_result or {}).get("depth_mm"),
        },
        "parts": compact_parts,
        "selected_estimate_items": [
            {
                "name": item.get("name"),
                "quantity": item.get("quantity"),
                "unit": item.get("unit"),
                "category": item.get("category"),
            }
            for item in items or []
        ],
        "estimator_context": str(instructions or "")[:4000],
    }

    response = client.responses.create(
        model="gpt-5.6-terra",
        tools=[{
            "type": "web_search",
            "user_location": {"type": "approximate", "country": "LT"},
        }],
        include=["web_search_call.action.sources"],
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": INSTRUCTIONS}],
            },
            {
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": json.dumps(request, ensure_ascii=False),
                }],
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "autonomous_market_estimate",
                "strict": True,
                "schema": AUTONOMOUS_SCHEMA,
            }
        },
    )
    return json.loads(response.output_text)


@st.fragment
def render_autonomous_estimate(ai_result, items, calculated_price, project_name, instructions=""):
    st.subheader("Autonomous market estimate")
    st.caption(
        "Optional internet-based comparison. It does not change the internal calculator or quotation. "
        "Running or refreshing it uses an additional AI web-search request."
    )

    payload = {
        "ai_result": ai_result,
        "items": items,
        "project_name": project_name,
        "instructions": instructions,
    }
    cache_key = _estimate_hash(payload)
    cache = st.session_state.setdefault("autonomous_estimate_cache", {})
    result = cache.get(cache_key)

    run_label = "Refresh autonomous estimate" if result else "Run autonomous market estimate"
    if st.button(run_label, key="autonomous_estimate_button"):
        try:
            with st.spinner("Researching comparable market prices..."):
                result = timed(
                    "autonomous_market_estimate",
                    autonomous_market_estimate,
                    ai_result,
                    items,
                    project_name,
                    instructions,
                )
            cache[cache_key] = result
            st.session_state["autonomous_estimate_result"] = result
            st.session_state["autonomous_estimate_result_key"] = cache_key
        except Exception as error:
            print("AUTONOMOUS ESTIMATE ERROR:", repr(error))
            st.error(f"Autonomous market estimate failed: {error}")
            return

    if not result and st.session_state.get("autonomous_estimate_result_key") == cache_key:
        result = st.session_state.get("autonomous_estimate_result")
    if not result:
        return

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Market low", f'€{float(result.get("low") or 0):,.0f}')
    with c2:
        st.metric("Market typical", f'€{float(result.get("typical") or 0):,.0f}')
    with c3:
        st.metric("Market high", f'€{float(result.get("high") or 0):,.0f}')
    with c4:
        st.metric("Internal calculator", f"€{float(calculated_price or 0):,.0f}")

    typical = float(result.get("typical") or 0)
    if typical > 0 and calculated_price:
        difference = (float(calculated_price) - typical) / typical * 100
        st.info(
            f'Internal calculated price is {abs(difference):.0f}% '
            f'{"above" if difference > 0 else "below"} the autonomous typical estimate.'
        )

    st.write(result.get("basis") or "")
    assumptions = result.get("assumptions") or []
    if assumptions:
        with st.expander("Assumptions"):
            for assumption in assumptions:
                st.markdown(f"- {assumption}")

    comparables = result.get("comparables") or []
    if comparables:
        st.dataframe(
            comparables,
            hide_index=True,
            width="stretch",
            column_config={"url": st.column_config.LinkColumn("Source")},
        )
