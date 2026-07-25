import asyncio
import json
import logging
import re
import time

from database import crud
from locales.texts import DEFAULT_LANG, get_text
from services.ai_tools import TOOL_SCHEMAS, execute_tool

from .client import ask_nvidia_raw, get_ai_response
from .formatting import strip_html_tags
from .language import _looks_like_action_request, _resolve_language

logger = logging.getLogger(__name__)

_AGENT_TOTAL_TIMEOUT_SECONDS = 45
_MAX_AGENT_ITERATIONS = 5  # protection against an infinite loop of tool calls

_BOLD_CONTENT = re.compile(r"<b>(.*?)</b>")


def _dedupe_tool_calls(tool_calls: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for call in tool_calls:
        name = call.get("function", {}).get("name", "")
        raw_args = call.get("function", {}).get("arguments") or "{}"
        try:
            normalized_args = json.dumps(json.loads(raw_args), sort_keys=True, ensure_ascii=False)
        except json.JSONDecodeError:
            normalized_args = raw_args
        key = f"{name}:{normalized_args}"
        if key in seen:
            logger.warning(f"[DEDUPE] Skipping duplicate tool_call: {name} {raw_args}")
            continue
        seen.add(key)
        deduped.append(call)
    return deduped


def _extract_known_names(tool_name: str, result: dict) -> set[str]:
    """
    Extracts the set of ACTUAL medicine/prescription names from a read-tool
    result — used to check whether the model's final answer is not
    "hallucinating" a name that wasn't in the DB data.
    """
    names: set[str] = set()
    if tool_name == "get_my_medicines":
        for m in result.get("medicines", []) or []:
            name = m.get("name")
            if name:
                names.add(str(name).strip().lower())
    elif tool_name == "get_my_prescriptions":
        for p in result.get("prescriptions", []) or []:
            name = p.get("medicine_name")
            if name:
                names.add(str(name).strip().lower())
    return names


def _find_ungrounded_names(final_text: str, known_names: set[str]) -> list[str]:
    if not known_names:
        return []
    mentioned = [m.strip().lower() for m in _BOLD_CONTENT.findall(final_text)]
    candidates = [m for m in mentioned if len(m) >= 3]
    return [m for m in candidates if not any(m in known or known in m for known in known_names)]


async def get_ai_agent_response(
    config,
    session,
    user_id: int,
    messages: list[dict],
    language: str = DEFAULT_LANG,
) -> tuple[str, str, dict | None]:
    start_time = time.monotonic()
    language = _resolve_language(messages, language)

    if not config.nvidia_api_key:
        text, model = await get_ai_response(config, messages, language)
        latency_ms = int((time.monotonic() - start_time) * 1000)
        await _log_metric(
            session,
            user_id,
            model_used=model,
            tool_choice=None,
            tool_names=None,
            latency_ms=latency_ms,
            status="success",
        )
        return strip_html_tags(text), model, None

    try:
        text, model, confirmation, meta = await asyncio.wait_for(
            _run_agent_loop(config, session, user_id, messages, language),
            timeout=_AGENT_TOTAL_TIMEOUT_SECONDS,
        )
        latency_ms = int((time.monotonic() - start_time) * 1000)
        await _log_metric(
            session,
            user_id,
            model_used=model,
            tool_choice=meta["tool_choice"],
            tool_names=meta["tool_names"],
            latency_ms=latency_ms,
            status="success",
        )
        return text, model, confirmation
    except asyncio.TimeoutError:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        logger.error(
            f"Agent loop exceeded the overall time limit ({_AGENT_TOTAL_TIMEOUT_SECONDS}s) for user_id={user_id}"
        )
        await _log_metric(
            session,
            user_id,
            model_used="none",
            tool_choice=None,
            tool_names=None,
            latency_ms=latency_ms,
            status="timeout",
            error_message=f"Exceeded {_AGENT_TOTAL_TIMEOUT_SECONDS}s limit",
        )
        return get_text(language, "ai_err_api"), "none", None


async def _log_metric(
    session,
    user_id: int,
    model_used: str,
    tool_choice: str | None,
    tool_names: list[str] | None,
    latency_ms: int,
    status: str,
    error_message: str | None = None,
) -> None:
    """
    Best-effort metric logging — a failure here (e.g. a stale session) must
    never break the actual AI response the user is waiting for.
    """
    try:
        await crud.log_ai_metric(
            session,
            user_id=user_id,
            model_used=model_used,
            tool_choice=tool_choice,
            tool_names=tool_names,
            latency_ms=latency_ms,
            status=status,
            error_message=error_message,
        )
    except Exception as e:
        logger.warning(f"Failed to log AI metric for user_id={user_id}: {e}")


async def _run_agent_loop(
    config,
    session,
    user_id: int,
    messages: list[dict],
    language: str,
) -> tuple[str, str, dict | None, dict]:
    conversation = list(messages)
    last_user_text = messages[-1].get("content", "") if messages else ""
    known_names: set[str] = set()
    retried_for_grounding = False
    called_tool_names: list[str] = []
    first_tool_choice: str | None = None

    try:
        for iteration in range(_MAX_AGENT_ITERATIONS):
            force_tool = iteration == 0 and not retried_for_grounding and _looks_like_action_request(last_user_text)
            tool_choice = "required" if force_tool else "auto"
            if first_tool_choice is None:
                first_tool_choice = tool_choice

            assistant_message = await ask_nvidia_raw(
                config.nvidia_api_key,
                config.nvidia_base_url,
                config.nvidia_model,
                conversation,
                tools=TOOL_SCHEMAS,
                language=language,
                tool_choice=tool_choice,
            )

            logger.debug(f"Raw NIM response (user_id={user_id}, iteration={iteration}): {assistant_message}")

            tool_calls = assistant_message.get("tool_calls")

            if not tool_calls:
                final_text = (assistant_message.get("content") or "").strip()

                ungrounded = _find_ungrounded_names(final_text, known_names)
                if ungrounded and not retried_for_grounding:
                    logger.warning(
                        f"[GROUNDING] The model mentioned data not present in "
                        f"the tool result: {ungrounded}. Making one retry."
                    )
                    conversation.append({"role": "assistant", "content": final_text})
                    conversation.append(
                        {
                            "role": "user",
                            "content": (
                                "System note: your previous answer mentioned data "
                                "that does not match the actual tool results. "
                                "Please re-answer using ONLY the exact names and "
                                "values from the tool results above."
                            ),
                        }
                    )
                    retried_for_grounding = True
                    continue

                meta: dict[str, str | list[str] | None] = {
                    "tool_choice": first_tool_choice,
                    "tool_names": called_tool_names,
                }
                return final_text, f"NVIDIA Agent ({config.nvidia_model})", None, meta

            tool_calls = _dedupe_tool_calls(tool_calls)

            conversation.append(
                {
                    "role": "assistant",
                    "content": assistant_message.get("content"),
                    "tool_calls": tool_calls,
                }
            )

            for call in tool_calls:
                tool_name = call["function"]["name"]
                called_tool_names.append(tool_name)
                raw_arguments = call["function"].get("arguments") or "{}"
                try:
                    parsed_arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    parsed_arguments = {}

                logger.info(f"Agent calling tool '{tool_name}' for user_id={user_id} with args={parsed_arguments}")

                result = await execute_tool(tool_name, session, user_id, parsed_arguments)

                if tool_name in ("get_my_medicines", "get_my_prescriptions"):
                    known_names |= _extract_known_names(tool_name, result)

                if result.get("requires_confirmation"):
                    meta = {"tool_choice": first_tool_choice, "tool_names": called_tool_names}
                    return (
                        "",
                        f"NVIDIA Agent ({config.nvidia_model})",
                        {
                            "target_type": result["target_type"],
                            "target_id": result["target_id"],
                            "target_name": result["target_name"],
                        },
                        meta,
                    )

                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        logger.error(f"The agent did not produce a final answer within {_MAX_AGENT_ITERATIONS} iterations")
        meta = {"tool_choice": first_tool_choice, "tool_names": called_tool_names}
        return get_text(language, "ai_err_api"), "none", None, meta

    except Exception as e:
        logger.error(f"NVIDIA agent loop error: {type(e).__name__}: {e}")
        text, model = await get_ai_response(config, messages, language)
        meta = {"tool_choice": first_tool_choice, "tool_names": called_tool_names}
        return strip_html_tags(text), model, None, meta
