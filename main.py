"""Grace — an AI assistant for church administration.

Flask backend that serves the chat UI and proxies conversations to the
Claude API with per-session conversation memory, plus a structured
"Sunday Prep" endpoint that generates a bulletin, slideshow content, and
announcer sheet from one set of weekly information.
"""

import datetime
import json
import os
import threading
import uuid

import anthropic
from flask import Flask, jsonify, request, send_from_directory

from prompts import FULL_SYSTEM_PROMPT, SUNDAY_PREP_PROMPT

MODEL = os.environ.get("GRACE_MODEL", "claude-opus-4-8")
MAX_TOKENS = int(os.environ.get("GRACE_MAX_TOKENS", "4096"))
SUNDAY_PREP_MAX_TOKENS = int(os.environ.get("GRACE_SUNDAY_PREP_MAX_TOKENS", "8192"))
# Keep the last N messages (user + assistant turns) per session so long
# conversations don't grow without bound.
MAX_HISTORY_MESSAGES = 40

# Schema for the Sunday Prep structured output: one generation returns the
# bulletin, the slide deck content, and the announcer script together so the
# facts in each stay consistent.
SUNDAY_PREP_SCHEMA = {
    "type": "object",
    "properties": {
        "bulletin": {
            "type": "string",
            "description": "Print-ready bulletin in Markdown",
        },
        "slides": {
            "type": "array",
            "description": "Projection slideshow content, one entry per slide",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {
                        "type": "string",
                        "description": "3-5 short lines separated by newlines",
                    },
                },
                "required": ["title", "body"],
                "additionalProperties": False,
            },
        },
        "announcer_sheet": {
            "type": "string",
            "description": "Spoken-word announcement script in Markdown",
        },
        "notes": {
            "type": "array",
            "description": "Missing info or inconsistencies the admin should check",
            "items": {"type": "string"},
        },
    },
    "required": ["bulletin", "slides", "announcer_sheet", "notes"],
    "additionalProperties": False,
}

app = Flask(__name__, static_folder="static")
client = anthropic.Anthropic()

# In-memory conversation store: {session_id: [{"role": ..., "content": ...}]}
# Fine for a single-process deployment; swap for Redis/a database if you
# scale to multiple workers.
conversations = {}
conversations_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Church profile: persisted to a JSON file so the admin enters their church's
# boilerplate (name, service times, standing announcements) exactly once.
# ---------------------------------------------------------------------------
PROFILE_PATH = os.environ.get("GRACE_PROFILE_PATH", "church_profile.json")
PROFILE_FIELDS = (
    "church_name",
    "service_times",
    "office_contact",
    "order_of_service",
    "standing_announcements",
    "notes",
)
profile_lock = threading.Lock()


def load_profile():
    try:
        with open(PROFILE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {k: str(data.get(k, "") or "") for k in PROFILE_FIELDS}


def save_profile(data):
    profile = {k: str(data.get(k, "") or "").strip() for k in PROFILE_FIELDS}
    with profile_lock:
        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2)
    return profile


# ---------------------------------------------------------------------------
# Usage tracking: per-day request and token counts, persisted to a JSON file
# so you can watch pilot usage without opening the Anthropic Console.
# ---------------------------------------------------------------------------
USAGE_PATH = os.environ.get("GRACE_USAGE_PATH", "grace_usage.json")
usage_lock = threading.Lock()

# claude-opus-4-8 per-million-token rates (USD), for the cost estimate only.
# Update these if you change GRACE_MODEL to a different pricing tier.
PRICE_PER_MTOK = {
    "input_tokens": 5.00,
    "output_tokens": 25.00,
    "cache_read_input_tokens": 0.50,
    "cache_creation_input_tokens": 6.25,
}
USAGE_TOKEN_FIELDS = tuple(PRICE_PER_MTOK)


def _load_usage():
    try:
        with open(USAGE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"days": {}}


def _estimated_cost(counts):
    return round(
        sum(counts.get(k, 0) * rate / 1_000_000 for k, rate in PRICE_PER_MTOK.items()),
        4,
    )


def record_usage(endpoint, usage):
    """Add one API response's usage to today's counters."""
    today = datetime.date.today().isoformat()
    with usage_lock:
        data = _load_usage()
        day = data["days"].setdefault(
            today, {"requests": {}, **{k: 0 for k in USAGE_TOKEN_FIELDS}}
        )
        day["requests"][endpoint] = day["requests"].get(endpoint, 0) + 1
        for field in USAGE_TOKEN_FIELDS:
            day[field] += getattr(usage, field, None) or 0
        with open(USAGE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def usage_summary():
    with usage_lock:
        data = _load_usage()
    days = data["days"]
    today = datetime.date.today().isoformat()
    totals = {"requests": 0, **{k: 0 for k in USAGE_TOKEN_FIELDS}}
    for day in days.values():
        totals["requests"] += sum(day["requests"].values())
        for field in USAGE_TOKEN_FIELDS:
            totals[field] += day.get(field, 0)
    recent = {
        date: {**counts, "estimated_cost_usd": _estimated_cost(counts)}
        for date, counts in sorted(days.items(), reverse=True)[:30]
    }
    return {
        "model": MODEL,
        "totals": {**totals, "estimated_cost_usd": _estimated_cost(totals)},
        "today": recent.get(today),
        "days": recent,
        "note": "Cost is an estimate from claude-opus-4-8 list prices; the Claude Console is authoritative.",
    }


def profile_context_block(profile):
    """Render the saved profile as a system-prompt context block for chat.

    Returned as a SECOND system block after the cached main prompt, so
    editing the profile never invalidates the big prompt's cache prefix.
    """
    lines = []
    labels = {
        "church_name": "Church name",
        "service_times": "Service times",
        "office_contact": "Church office contact",
        "order_of_service": "Usual order of service",
        "standing_announcements": "Standing weekly announcements",
        "notes": "Notes (pronunciations, preferences, etc.)",
    }
    for key, label in labels.items():
        if profile.get(key):
            lines.append(f"{label}: {profile[key]}")
    if not lines:
        return None
    return (
        "## This Church's Saved Profile\n"
        "The administrator saved this profile — use it instead of asking for "
        "these details again:\n" + "\n".join(lines)
    )


def call_claude(**kwargs):
    """Call the Claude API, mapping failures to (None, (json, status)).

    Returns (response, None) on success or (None, error_tuple) on failure.
    """
    try:
        return client.messages.create(**kwargs), None
    except anthropic.AuthenticationError:
        return None, (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid or missing API key. Set ANTHROPIC_API_KEY and restart the server.",
                }
            ),
            500,
        )
    except anthropic.RateLimitError:
        return None, (
            jsonify(
                {
                    "success": False,
                    "error": "We're a little busy right now — please try again in a moment.",
                }
            ),
            429,
        )
    except anthropic.APIStatusError as e:
        return None, (
            jsonify({"success": False, "error": f"API error: {e.message}"}),
            502,
        )
    except anthropic.APIConnectionError:
        return None, (
            jsonify(
                {
                    "success": False,
                    "error": "Could not reach the Claude API. Check the server's network connection.",
                }
            ),
            502,
        )
    except TypeError as e:
        # The SDK raises TypeError when no credentials are configured at all.
        if "authentication" in str(e).lower():
            return None, (
                jsonify(
                    {
                        "success": False,
                        "error": "No API key configured. Set ANTHROPIC_API_KEY and restart the server.",
                    }
                ),
                500,
            )
        raise
    except Exception:
        app.logger.exception("Unexpected error calling the Claude API")
        return None, (
            jsonify(
                {
                    "success": False,
                    "error": "Something unexpected went wrong. Please try again.",
                }
            ),
            500,
        )


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "model": MODEL})


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not user_message:
        return jsonify({"success": False, "error": "Message cannot be empty."}), 400

    with conversations_lock:
        history = list(conversations.get(session_id, []))

    messages = history + [{"role": "user", "content": user_message}]

    system_blocks = [
        {
            "type": "text",
            "text": FULL_SYSTEM_PROMPT,
            # Cache the large, stable system prompt so repeat
            # requests read it at ~10% of the input price.
            "cache_control": {"type": "ephemeral"},
        }
    ]
    profile_block = profile_context_block(load_profile())
    if profile_block:
        # Placed after the cache breakpoint so profile edits don't
        # invalidate the cached main prompt.
        system_blocks.append({"type": "text", "text": profile_block})

    response, error = call_claude(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=system_blocks,
        messages=messages,
    )
    if error:
        return error
    record_usage("chat", response.usage)

    if response.stop_reason == "refusal":
        return jsonify(
            {
                "success": True,
                "session_id": session_id,
                "response": "I'm sorry, I can't help with that request. Is there something else I can do for you?",
            }
        )

    response_text = "".join(
        block.text for block in response.content if block.type == "text"
    )

    with conversations_lock:
        history = conversations.setdefault(session_id, [])
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": response_text})
        # Trim to the most recent turns, keeping the list starting on a
        # user message.
        if len(history) > MAX_HISTORY_MESSAGES:
            del history[: len(history) - MAX_HISTORY_MESSAGES]
            while history and history[0]["role"] != "user":
                del history[0]

    return jsonify(
        {"success": True, "session_id": session_id, "response": response_text}
    )


@app.route("/api/sunday-prep", methods=["POST"])
def sunday_prep():
    """Generate a bulletin, slideshow content, and announcer sheet from one
    set of weekly service info, guaranteed consistent because it's a single
    structured generation."""
    data = request.get_json(silent=True) or {}

    profile = load_profile()

    # Blank request fields fall back to the saved church profile.
    church_name = (data.get("church_name") or "").strip() or profile.get("church_name", "")
    service_date = (data.get("service_date") or "").strip()
    service_time = (data.get("service_time") or "").strip() or profile.get("service_times", "")
    sermon = (data.get("sermon") or "").strip()
    order_of_service = (data.get("order_of_service") or "").strip() or profile.get(
        "order_of_service", ""
    )
    announcements = (data.get("announcements") or "").strip()
    extra_notes = (data.get("extra_notes") or "").strip()

    if not announcements:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Please add this week's announcements — that's the one thing Grace can't guess.",
                }
            ),
            400,
        )

    parts = []
    if church_name:
        parts.append(f"Church name: {church_name}")
    if service_date:
        parts.append(f"Service date: {service_date}")
    if service_time:
        parts.append(f"Service time(s): {service_time}")
    if sermon:
        parts.append(f"Sermon / message: {sermon}")
    if order_of_service:
        parts.append(f"Order of service:\n{order_of_service}")
    parts.append(f"This week's announcements:\n{announcements}")
    if profile.get("standing_announcements"):
        parts.append(
            "Standing weekly announcements (include these every week, after "
            f"this week's items):\n{profile['standing_announcements']}"
        )
    if profile.get("office_contact"):
        parts.append(
            f"Church office contact (use where a contact is needed): {profile['office_contact']}"
        )
    if profile.get("notes"):
        parts.append(f"Saved church notes (pronunciations, preferences):\n{profile['notes']}")
    if extra_notes:
        parts.append(f"Additional notes:\n{extra_notes}")

    response, error = call_claude(
        model=MODEL,
        max_tokens=SUNDAY_PREP_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": SUNDAY_PREP_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        output_config={
            "format": {"type": "json_schema", "schema": SUNDAY_PREP_SCHEMA}
        },
        messages=[{"role": "user", "content": "\n\n".join(parts)}],
    )
    if error:
        return error
    record_usage("sunday_prep", response.usage)

    if response.stop_reason == "refusal":
        return (
            jsonify({"success": False, "error": "I'm sorry, I can't help with that request."}),
            400,
        )
    if response.stop_reason == "max_tokens":
        return (
            jsonify(
                {
                    "success": False,
                    "error": "That week's info was too long to process in one pass. Try trimming the announcements or splitting them up.",
                }
            ),
            400,
        )

    raw = next((b.text for b in response.content if b.type == "text"), "")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        app.logger.error("Sunday Prep returned unparseable output: %.200s", raw)
        return (
            jsonify(
                {"success": False, "error": "Grace produced an unreadable result. Please try again."}
            ),
            502,
        )

    return jsonify({"success": True, **result})


@app.route("/api/usage")
def usage():
    return jsonify({"success": True, **usage_summary()})


@app.route("/api/profile", methods=["GET", "POST"])
def profile():
    if request.method == "GET":
        return jsonify({"success": True, "profile": load_profile()})
    data = request.get_json(silent=True) or {}
    saved = save_profile(data)
    return jsonify({"success": True, "profile": saved})


@app.route("/api/reset", methods=["POST"])
def reset():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    if session_id:
        with conversations_lock:
            conversations.pop(session_id, None)
    return jsonify({"success": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
