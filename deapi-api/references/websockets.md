# WebSockets

Live job updates with progress and image previews over a Pusher-compatible connection. Best
for user interfaces; pair it with webhooks or polling for reliability. Docs:
https://docs.deapi.ai/execution-modes-and-integrations/websockets.md.

| Setting | Value |
|---|---|
| Host | `soketi.deapi.ai`, port 443, TLS (`wss`) |
| App key | the public app key listed in the WebSockets docs |
| Auth endpoint | `POST https://api.deapi.ai/broadcasting/auth` with `{"socket_id", "channel_name"}` and `Authorization: Bearer <key>` |
| Channel | `private-client.<client_id>`; the client ID is the UUID shown on the dashboard |
| Event | `request.status.updated` with `request_id`, `status`, `progress`, `preview`, `result_url` |

Clients: `pusher-js` (optionally with `laravel-echo`, which uses `client.<id>` and event
`.request.status.updated`), or any Pusher protocol library. Python SDK: `client.realtime()`.

## Keep the key on the server

The channel authorisation needs your deAPI key. In a browser app, do **not** call
`/broadcasting/auth` from the page with the key. Give `pusher-js` an authorizer that calls
**your** backend, which checks the user's session and forwards the request to deAPI:

```javascript
// Browser: ask your own backend to authorise the channel.
const pusher = new Pusher(DEAPI_WS_APP_KEY, {
  wsHost: "soketi.deapi.ai", wsPort: 443, wssPort: 443, forceTLS: true,
  enabledTransports: ["ws", "wss"], cluster: "mt1",
  channelAuthorization: { endpoint: "/api/deapi/ws-auth", transport: "ajax" },
});
pusher.subscribe(`private-client.${CLIENT_ID}`).bind("request.status.updated", (e) => {
  showProgress(e.request_id, e.status, e.progress, e.preview);
});
```

```javascript
// Server (Express): forward the authorisation with the key from the environment.
app.post("/api/deapi/ws-auth", express.urlencoded({ extended: false }), requireLogin, async (req, res) => {
  const r = await fetch("https://api.deapi.ai/broadcasting/auth", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${process.env.DEAPI_API_KEY}` },
    body: JSON.stringify({ socket_id: req.body.socket_id, channel_name: req.body.channel_name }),
  });
  res.status(r.status).json(await r.json());
});
```

One channel carries the events of every job of the account, so a multi-tenant app must not
forward raw events to end users. Filter by `request_id` on your server, or show only progress.

## Behaviour to plan for

- Failed jobs are not always announced on the socket; poll `GET /api/v2/jobs/{id}` now and then
  while waiting, and after every reconnect. Events sent while disconnected are lost.
- `progress` and `preview` are for display. Take the final result (fresh `result_url`, `price`,
  `result`) from `GET /api/v2/jobs/{id}` once the event says `done`.
- `preview` for image jobs is an image data URI; treat it as untrusted content.
