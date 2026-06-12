# ai-datacenters

A hand-curated tracker of the largest **AI datacenters** under construction, with
on-demand "what's new?" lookups via the Grok API (live web search enabled).

**Live dashboard:** <https://ai-datacenters.lowlandcapital.com>

The static-list-of-mega-projects approach is the deliberate scope of v1: rather
than scraping DataCenterDynamics or parsing air-permits, you keep a curated list
and ask Grok — with live web search — to summarize what's new per project,
on a click of a button. The whole history of every check is stored, so you can
audit Grok's answer (and citations) months later.

## How it works

1. **Discover** — clicking *Discover projects* asks Grok (with web search) for
   the top N largest in-progress AI datacenters, returned as strict JSON, and
   upserts them into SQLite. Idempotent; rerunning won't duplicate.
2. **Check** — each row has a *Check now* button. The backend builds a prompt
   containing the project's known state + last-checked date, asks Grok with
   live web search what's new since then, and stores both the parsed update and
   the raw response + citations in `dc_checks`.
3. **Render** — the table shows current status, latest summary, and last-checked
   date. Updated rows briefly flash yellow.

| Endpoint | What it does |
|---|---|
| `GET /` | The table |
| `GET /api/projects` | JSON of current projects |
| `POST /api/discover` | Run a Grok discovery (seeds new projects) |
| `POST /api/check/{slug}` | Run a Grok "what's new" check for one project |
| `GET /api/project/{slug}/history` | Last 20 checks for one project |

## Why Grok

Live web search is built in (the `search_parameters` field on the
OpenAI-compatible chat completions endpoint), and the model is cheap relative
to how much reading it does on each call — typical check costs $0.005–0.02.

## Local development

```bash
export XAI_API_KEY=xai-...
uv sync
uv run uvicorn ai_datacenters.web:app --reload --port 8088
```

Then <http://127.0.0.1:8088>.

## Config

[`config.toml`](./config.toml) — DB path, model id, search mode, discover target
count. No code changes needed to tune.

## Deploy

See [`deploy/README.md`](./deploy/README.md) — systemd `--user` service + Caddy
vhost for `ai-datacenters.lowlandcapital.com`.

## License

MIT.
