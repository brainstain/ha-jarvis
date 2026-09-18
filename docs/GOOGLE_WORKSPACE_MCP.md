# Google Workspace MCP (Gmail / Docs / Drive)

Adds Gmail, Google Docs, and Google Drive tools to agent-orchestrator via
[`workspace-mcp`](https://github.com/taylorwilsdon/google_workspace_mcp)
(PyPI: `workspace-mcp`), wired the same way as the other stdio MCP servers in
`custom-software/agent-orchestrator/config/mcp_servers.json` — it just runs as
a subprocess of the orchestrator, spawned on first use.

Server entry: `google-workspace` in `mcp_servers.json`, `enabled: true`.
**It's safe to leave enabled with no credentials set** — `MCPToolRegistry.discover()`
(`src/agent/mcp/registry.py`) tries each configured server independently and
catches failures per-server, so a broken/uncredentialed `google-workspace`
just means those tools aren't registered; nothing else breaks, and the
orchestrator's `/health` will show it in `mcp_servers_failed` (or similar —
check the health payload) until it's fixed. No restart needed once
credentials land — the next tool call retries the connection from scratch.

## 1. Google Cloud OAuth client (one-time, in the Google Cloud Console)

1. Create/select a project at console.cloud.google.com.
2. Enable the APIs you want: Gmail API, Google Docs API, Google Drive API.
3. OAuth consent screen: "External" is fine for personal use (your own
   account only, unpublished/testing mode never expires for you as the
   developer).
4. Create an OAuth client ID, type "Desktop app" (simplest — no redirect URI
   juggling for the stdio/legacy flow this deployment uses).
5. Note the client ID and client secret.

## 2. Set credentials for the container

In the repo root `.env` (gitignored, already how `HA_TOKEN` etc. are handled —
`scripts/deploy.sh` copies it to each node):

```
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
```

Redeploy the agent node (`scripts/deploy.sh deploy agent`) so the container
picks up the new env vars and the `google_credentials` volume (declared in
`servers/agent/docker-compose.yml`) exists.

## 3. Complete the OAuth consent flow

This is the part that doesn't fit the headless-node pattern used elsewhere in
this repo: Google's consent flow needs a browser redirect back to
`localhost`, and the agent node (192.168.13.22) has no browser. Two ways to
clear it:

- **SSH tunnel (simplest):** `ssh -L 8080:localhost:8080 michael@192.168.13.22`
  (or whatever port `workspace-mcp`'s local OAuth callback listens on — check
  `docker logs agent-orchestrator` for the printed consent URL and port the
  first time a Gmail/Docs/Drive tool is actually invoked), then open the
  printed URL in a browser on your Mac. The redirect lands on `localhost:8080`
  on your Mac, which the tunnel forwards to the container.
- **Run the consent step locally first:** `uvx workspace-mcp` on your Mac with
  the same `GOOGLE_OAUTH_CLIENT_ID`/`SECRET`, complete consent there, then copy
  the resulting token cache into the `google_credentials` volume on the node.

Once consent completes, `workspace-mcp` caches a refresh token on disk (its
default local credential store — a `.credentials/` directory, which is why
it's mounted at `/app/.credentials` via the `google_credentials` named volume
in docker-compose, so it survives container recreates). **Verify this path
against `docker exec agent-orchestrator workspace-mcp --help` /
the project's own docs before relying on it** — it wasn't confirmed against
the running container, only inferred from the project's README (which says
"never commit ... `.credentials/`" but doesn't spell out whether that's
relative to the process cwd or `$HOME`).

## 4. Tool scope

`mcp_servers.json` passes `--tool-tier core --tools gmail drive docs calendar`
to scope it down from the package's full ~120 tools. Cross-check `gmail`/
`drive`/`docs`/`calendar` are the right `--tools` slugs for your installed
version (`workspace-mcp --help`) — the README only confirmed those four as
example values.

`calendar` was added 2026-09-17 to trial as the canonical calendar path in
place of `mcp-calendar` (the HA-backed server, now `enabled: false`) — its
`get_events` tool supports keyword search (`query=`) and an open-ended
forward time range (omit `time_max`), unlike `mcp-calendar`'s HA-proxied
tool, which requires an explicit `start`/`end` window and has no search.
Because `google-workspace`'s server-wide `categories` is `["google"]`, which
isn't in `router.TOOL_CATEGORIES` and so is unreachable, the calendar tools
(`list_calendars`, `get_events`, `manage_event`, `manage_out_of_office`,
`manage_focus_time`, `query_freebusy`, `create_calendar`) get a per-tool
category override to `"calendar"` in `registry.py`'s
`_TOOL_CATEGORY_OVERRIDES` — see that module for why. Gmail/Drive/Docs tools
remain under `"google"`, which today is still unreachable by the router (a
pre-existing gap from PR #14, not fixed here).

Also note `tool_routing.max_tools_per_request` in `mcp_servers.json` is
capped at 7, and only `memory`/`notifications` are in
`always_include_categories`. If Gmail/Docs tools aren't showing up when
expected, check whether `category_priority` needs `"google"` added.

## Known gaps / not done here

- `celery-worker` (Phase 3 async tasks) does **not** get
  `GOOGLE_OAUTH_CLIENT_ID/SECRET` or the `google_credentials` volume — only
  `agent-orchestrator` (the interactive path) does. Add both if a background
  graph needs Gmail/Docs/Drive.
- Scopes are whatever `workspace-mcp` requests by default for the enabled
  services (e.g. full Gmail modify, not read-only) — pass `--read-only` or
  per-service `--permissions` in `mcp_servers.json`'s `args` if you want
  something narrower before running consent.
