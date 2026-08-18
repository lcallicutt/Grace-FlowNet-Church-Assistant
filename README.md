# Grace Ministry Hub

**Your church's central workspace for service preparation, volunteer coordination, communications, events, and ministry planning.**

Grace Ministry Hub helps church staff, pastors, and volunteers with:

- **Weekly Service Builder** — enter the week's information once; Grace creates your **bulletin**, **projection content**, and **announcer sheet** with consistent dates, times, and contact information — whatever day your church holds services
- **Church Profile** — save your church's name, service times, office contact, usual order of service, and standing weekly announcements once; chat uses them as context and the Weekly Service Builder fills them in automatically every week
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

To protect a shared deployment (e.g. a church pilot), also set an access code
before starting the server — visitors then enter it once on a lock screen:

   ```bash
   export GRACE_ACCESS_CODE=your-chosen-passcode
   ```

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — (required) | Your Claude API key |
| `GRACE_MODEL` | `claude-opus-4-8` | Which Claude model to use |
| `GRACE_MAX_TOKENS` | `4096` | Max chat response length |
| `GRACE_SERVICE_BUILDER_MAX_TOKENS` | `8192` | Max Weekly Service Builder response length |
| `GRACE_ACCESS_CODE` | — (unset = open) | When set, the app shows a lock screen and every API request must carry this code |
| `GRACE_PROFILE_PATH` | `church_profile.json` | Where the church profile is stored |
| `PORT` | `5000` | Server port |

## API

| Endpoint | Method | Body | Purpose |
| --- | --- | --- | --- |
| `/api/chat` | POST | `{"message": "...", "session_id": "..."}` | Send a message; returns `{"success", "session_id", "response"}` |
| `/api/weekly-service` | POST | `{"announcements": "...", "church_name", "service_date", "service_time", "sermon", "order_of_service", "extra_notes"}` (only `announcements` required) | Returns `{"bulletin", "slides": [{"title", "body"}], "announcer_sheet", "notes"}` |
| `/api/profile` | GET / POST | POST: `{"church_name", "service_times", "office_contact", "order_of_service", "standing_announcements", "notes"}` | Read or save the church profile (persisted to `church_profile.json`) |
| `/api/usage` | GET | — | Per-day request counts, token usage, and an estimated cost in USD (persisted to `grace_usage.json`) |
| `/api/auth` | GET / POST | POST: `{"code": "..."}` | GET: is an access code required? POST: verify a code |
| `/api/reset` | POST | `{"session_id": "..."}` | Clear a conversation |
| `/api/health` | GET | — | Health check |

### Weekly Service Builder

The **Weekly Service Builder** tab is built for the weekly grind: bulletins,
projection updates, and the announcer sheet — for services on any day of the
week. It uses a single structured Claude
generation (JSON schema output) to produce all three at once, so the facts
can't drift between formats. Each result tab has copy and download buttons,
and Grace lists anything to double-check (missing RSVP contacts, ambiguous
dates) in a "Grace noticed" callout instead of inventing details.

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
