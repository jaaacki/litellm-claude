"""Deterministic repair helpers for brittle Claude Code tool calls."""

import json
import logging

log = logging.getLogger("litellm-proxy.v2.tool_repair")


def repair_tool_call(tool_name, arguments, raw_arguments=None):
    if tool_name != "SendMessage" or not isinstance(arguments, dict):
        return arguments, raw_arguments, False

    repaired = dict(arguments)
    changed = False

    message = repaired.get("message")
    summary = repaired.get("summary")
    message_text = message.strip() if isinstance(message, str) else ""
    summary_text = summary.strip() if isinstance(summary, str) else ""

    if not message_text:
        synthesized_message = _synthesize_send_message(summary_text)
        if synthesized_message:
            repaired["message"] = synthesized_message
            message_text = synthesized_message
            changed = True

    if not summary_text and message_text:
        repaired["summary"] = _summarize_send_message(message_text)
        changed = True

    repaired_raw = raw_arguments
    if changed:
        repaired_raw = json.dumps(repaired, separators=(",", ":"))
        log.warning(
            "Repaired SendMessage tool call (fields=%s)",
            ",".join(sorted(_repaired_fields(arguments, repaired))),
        )
    return repaired, repaired_raw, changed


def _repaired_fields(original, repaired):
    fields = []
    for key, value in repaired.items():
        if original.get(key) != value:
            fields.append(key)
    return fields


def _synthesize_send_message(summary_text):
    if not summary_text:
        return None
    if "shutdown" in summary_text.lower():
        return json.dumps(
            {
                "type": "shutdown_request",
                "reason": "All work complete. Shutting down.",
            },
            separators=(",", ":"),
        )
    return summary_text


def _summarize_send_message(message_text):
    structured_summary = _structured_message_summary(message_text)
    if structured_summary:
        return structured_summary

    collapsed = " ".join(message_text.split())
    if not collapsed:
        return "Message"
    first_line = collapsed.split(". ", 1)[0].strip()
    summary = first_line or collapsed
    if len(summary) > 80:
        summary = summary[:77].rstrip() + "..."
    return summary


def _structured_message_summary(message_text):
    try:
        payload = json.loads(message_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    payload_type = str(payload.get("type", "")).strip().lower()
    if payload_type == "shutdown_request":
        return "Shutdown now"
    if payload_type:
        return payload_type.replace("_", " ").title()
    return None
