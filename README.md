# Grace — Church Administration Assistant

Grace is a web app that helps church staff, pastors, and volunteers with:

- **Volunteer scheduling** — greeters, ushers, nursery, tech booth, and more, with rotation patterns and backups
- **Communications** — announcements, bulletins, newsletters, and Facebook/Instagram posts
- **Event planning** — timelines, checklists, supplies, and volunteer needs for potlucks, retreats, VBS, holiday services
- **Ministry support** — small groups, Bible studies, youth programs, outreach planning

Built with Flask and the Claude API.

## Quick Start

1. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

2. **Set your API key** (get one at https://platform.claude.com)

   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   ```

3. **Run the server**

   ```bash
   python main.py
   ```

4. Open http://localhost:5000 and start chatting with Grace.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — (required) | Your Claude API key |
| `GRACE_MODEL` | `claude-opus-4-8` | Which Claude model to use |
| `GRACE_MAX_TOKENS` | `4096` | Max response length |
| `PORT` | `5000` | Server port |

## API

| Endpoint | Method | Body | Purpose |
| --- | --- | --- | --- |
| `/api/chat` | POST | `{"message": "...", "session_id": "..."}` | Send a message; returns `{"success", "session_id", "response"}` |
| `/api/reset` | POST | `{"session_id": "..."}` | Clear a conversation |
| `/api/health` | GET | — | Health check |

Conversation memory is per `session_id` and kept in server memory — Grace
remembers your church name, service times, and volunteers within a
conversation. The last 40 messages are retained per session.

## Try These Prompts

- "Schedule 5 greeters for Sunday March 9th at 9am"
- "Draft an announcement for our Easter egg hunt on April 12 at 10am"
- "Help me plan a youth retreat for May 15–17"
- "Our church is Greater Emmanuel and we have services at 9am and 11am" — then ask Grace to schedule greeters and watch it ask which service

## Project Layout

```
main.py             Flask app + Claude API integration
prompts.py          Grace's system prompt and enhancements
static/index.html   Chat UI (self-contained HTML/CSS/JS)
requirements.txt    Python dependencies
```

## Notes

- The system prompt uses Claude's prompt caching, so the large Grace prompt
  is billed at ~10% after the first request in each 5-minute window.
- Conversation state is in-process memory; for multi-worker or production
  deployments, back it with Redis or a database.
