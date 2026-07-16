"""Grace — an AI assistant for church administration.

Flask backend that serves the chat UI and proxies conversations to the
Claude API with per-session conversation memory.
"""

import os
import threading
import uuid

import anthropic
from flask import Flask, jsonify, request, send_from_directory

from prompts import FULL_SYSTEM_PROMPT

MODEL = os.environ.get("GRACE_MODEL", "claude-opus-4-8")
MAX_TOKENS = int(os.environ.get("GRACE_MAX_TOKENS", "4096"))
# Keep the last N messages (user + assistant turns) per session so long
# conversations don't grow without bound.
MAX_HISTORY_MESSAGES = 40

app = Flask(__name__, static_folder="static")
client = anthropic.Anthropic()

# In-memory conversation store: {session_id: [{"role": ..., "content": ...}]}
# Fine for a single-process deployment; swap for Redis/a database if you
# scale to multiple workers.
conversations = {}
conversations_lock = threading.Lock()


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

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": FULL_SYSTEM_PROMPT,
                    # Cache the large, stable system prompt so repeat
                    # requests read it at ~10% of the input price.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
        )
    except anthropic.AuthenticationError:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid or missing API key. Set ANTHROPIC_API_KEY and restart the server.",
                }
            ),
            500,
        )
    except anthropic.RateLimitError:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "We're a little busy right now — please try again in a moment.",
                }
            ),
            429,
        )
    except anthropic.APIStatusError as e:
        return jsonify({"success": False, "error": f"API error: {e.message}"}), 502
    except anthropic.APIConnectionError:
        return (
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
            return (
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
        app.logger.exception("Unexpected error in /api/chat")
        return (
            jsonify(
                {"success": False, "error": "Something unexpected went wrong. Please try again."}
            ),
            500,
        )

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
