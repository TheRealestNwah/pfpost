# pfpost

Post to Pixelfed from the command line or a desktop app, now or on a schedule.

Works with any Mastodon-compatible Pixelfed instance. Credentials go into your
operating system's credential store, never into a file in the clear. The core
is pure standard library; the only dependencies are `keyring` for cross-platform
secret storage and `PySide6` for the optional GUI.

Verified end to end against **gram.social** (Pixelfed 0.12.9) — registration,
authorization, and live posting from both the CLI and the desktop app —
including automatic handling of instances whose web server caps request header
size.

## Why it works this way

Pixelfed has **no server-side post scheduling** — there is no `scheduled_at`
parameter on `POST /api/v1/statuses`. Anything that "schedules" a Pixelfed post
is really a local queue plus a clock. That is what `queue` + Windows Task
Scheduler provide here.

Posting is always two steps: upload each file to `/api/v1/media` to get an id,
then create the status referencing those ids. Pixelfed requires at least one
media attachment — text-only posts are rejected.

## Install

```
pip install -r requirements.txt
```

`keyring` is strongly recommended — without it pfpost falls back to Windows
DPAPI, and on other platforms it will refuse to store credentials rather than
write them somewhere insecure. `PySide6` is only needed for the GUI.

## Setup

```
python pfpost.py register --instance your.instance
python pfpost.py auth
```

`register` hits `/api/v1/apps`, which needs no authentication and returns the
client secret directly, so there is no value to transcribe. `auth` opens your
browser, you approve, and a one-shot listener on `localhost:8080` catches the
code and exchanges it for a token.

Scopes are chosen automatically — see below.

### Why not the instance's Developers page

Clients created through `/settings/developers` on gram.social were rejected at
`/oauth/token` with `invalid_client` — twice, with two different clients. The
secret that page displays does not appear to be the value the token endpoint
validates against. `/api/v1/apps` avoids the problem entirely, and works the
same way on every Mastodon-compatible server.

### Automatic scope selection

Some instances sit behind an nginx with `large_client_header_buffers` at 1k,
which rejects a normal Passport token **before Pixelfed ever sees it**. Laravel
Passport's JWT length depends on the scope list:

| Scopes | Token length | Result on such a host |
|---|---|---|
| `read write` | ~1007 chars | **400 Request Header Or Cookie Too Large** |
| `write` | ~1000 chars | works — right at the ceiling |

`register` probes the host by binary search before choosing, and drops to
`write` only when it must. On a normally configured instance you get `read
write` and a working `whoami`. Override with `--scopes` if you need to.

On gram.social specifically the ceiling is exactly 1000 characters, which leaves
**zero margin** — if Pixelfed ever adds a JWT claim, posting breaks with no
warning, most likely at the annual token refresh. The real fix belongs on the
server:

```
large_client_header_buffers 4 16k;
```

Worth reporting to any instance admin who hits this; it breaks every OAuth API
client on that host, not just this one.

## Where credentials live

Secrets are never written to disk in the clear. Three backends, in order:

| Backend | When | Notes |
|---|---|---|
| `keyring` | `keyring` installed | Credential Manager / Keychain / Secret Service |
| `dpapi` | Windows, no keyring | Bound to your Windows user account |
| none | anything else | pfpost refuses to store rather than write plaintext |

Non-secret settings live in `state.json` under `%APPDATA%\PixelfedPoster\`
(or `$XDG_CONFIG_HOME`). `pfpost whoami` reports which backend is active.

## Layout

```
pfpost/
  api.py       Pixelfed client and OAuth - no UI, no printing
  store.py     config and credential storage
  session.py   registration, authorization, token lifecycle
  queue.py     local scheduling queue
  cli.py       command line interface
  gui.py       desktop interface (PySide6)
pfpost.py      launcher for running from a checkout
```

Both frontends drive the same core, so anything the CLI can do the GUI can too.

## Desktop app

```
python pfpost.py gui
```

Drag images onto the window, edit alt text inline per image, write a caption
against a live character counter, then post immediately or add it to the queue
with a date picker. The queue table below shows pending, posted and failed
items; hover a failed row to see the error.

Every network call runs on a worker thread, so the window never freezes during
an upload. If no account is connected the connect dialog opens on launch: enter
your instance, and it registers the client and runs the browser authorization
for you.

A bar across the top always shows whether an account is connected — green with
your `@name`, amber when a client is registered but not yet authorized, red when
there is nothing set up. Posting is disabled until you connect, so the window
never looks usable when it isn't.

Needs `PySide6`. The CLI works without it.

## Checking and clearing the connection

```
python pfpost.py whoami
python pfpost.py logout
python pfpost.py logout --keep-client
```

`whoami` reports connection state whatever it is, including when the token has
no `read` scope and the account name cannot be fetched. In the GUI the same is
in the account bar, and **Account > Disconnect** offers the two options:

- **Sign out, keep client** — drops the token, keeps the registration, so
  signing back in is one browser trip.
- **Remove everything** — drops the client too; reconnecting re-registers.

Either way the credentials go from this machine only. **Pixelfed exposes no
token-revocation endpoint**, so the token stays valid on the server until you
revoke it under `Settings > Applications` on your instance. Both the CLI and the
dialog say so and link there rather than implying a full sign-out.

## Posting

```
python pfpost.py post photo.jpg --caption "Morning fog" --alt "Fog over a valley at dawn"
```

Multiple images, one alt each, in order:

```
python pfpost.py post a.jpg b.jpg --caption "Two views" --alt "First" --alt "Second"
```

One alt applied to every image:

```
python pfpost.py post a.jpg b.jpg --caption "Series" --alt "Untitled study"
```

Flags:

- `--visibility public|unlisted|private` (default `public`)
- `--dry-run` validates files, caption length and attachment count without uploading

Check what the instance allows (works without auth):

```
python pfpost.py info
```

gram.social as of this writing: 2000 character captions, 20 attachments,
38.1 MB per image, accepting jpeg / png / gif / webp / avif / heic / mp4 / mov.
`post` reads these before uploading and refuses early rather than letting the
server return an opaque 422 halfway through.

## Scheduling

Add to the queue — absolute local time, or relative:

```
python pfpost.py queue add photo.jpg --caption "Later" --at "2026-09-10 17:00"
python pfpost.py queue add photo.jpg --caption "Soon" --at +2h
```

Inspect and manage:

```
python pfpost.py queue list
python pfpost.py queue list --all
python pfpost.py queue remove abc12345
```

Publish everything due:

```
python pfpost.py queue run
```

To automate that, print the registration commands and paste them into
PowerShell:

```
python pfpost.py schedule --every 15
```

`-StartWhenAvailable` is the important flag — it makes the task catch up after
sleep or a reboot instead of silently skipping a missed window. `pythonw.exe` is
used so nothing flashes a console window every 15 minutes.

## Behaviour worth knowing

- **Retries.** A failed queue item is retried on the next run, up to three
  attempts, then marked `failed` with the error preserved. Never silently
  dropped.
- **Missing files.** If an image has moved or been deleted by the time the item
  comes due, it is marked `failed` immediately and nothing is uploaded.
- **Token refresh.** Tokens last a year. `pfpost` refreshes automatically once
  fewer than 7 days remain. Watch for the header-size issue above if it ever
  starts failing after a refresh.
- **`whoami` needs `read` scope** to show your account name. Without it, it
  still reports instance, scopes, storage backend and token expiry.
- **Logging out is local.** Pixelfed has no revocation endpoint; revoke on the
  instance under Settings > Applications if you need the token dead server-side.
- **Alt text** is sent as `description` on the media upload, not on the status.
- **State** lives in `%APPDATA%\PixelfedPoster\` — `state.json` for credentials,
  `queue.json` for pending posts. Queued items reference images by path, so
  don't move a file between queueing and posting.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `400 Request Header Or Cookie Too Large` | Token exceeds the server's header limit. See the scopes section above. |
| `invalid_client` at `/oauth/token` | Client secret isn't valid. Re-run `register` rather than using the Developers page. |
| `403` on posting | Token lacks `write`. Re-run `register` then `auth`. |
| `401` | Token revoked — re-run `auth`. |
| `429` | Rate limited; `/oauth/token` is capped at 10 requests/minute. |
| Redirect never arrives | Redirect URL on the client must match `http://localhost:8080/callback` exactly. |
| Port 8080 busy | Another dev server has it. Register with `--port` and re-run `auth`. |
| `CryptUnprotectData failed` | Credential was created by a different Windows user. Re-run `register`. |
| `No secure credential store is available` | Install keyring: `pip install keyring`. |
| `can't open file 'pfpost.py'` | Wrong working directory — `cd "D:\Projects\Pixelfed Poster"` first. |
| `&&` is not a valid statement separator | Windows PowerShell 5.1. Use `;`, or run `pwsh`. |

## Tests

```
python test_pfpost.py
python test_gui.py
```

39 checks against a mock Pixelfed on port 47311, exercising the real request
path: credential storage round-trip, header-limit probing and scope selection,
app registration, token auto-refresh, validation, multipart encoding,
`media_ids[]` ordering, queue due-time selection, retry behaviour and
missing-file handling. The mock can simulate an nginx header cap, so the
scope-narrowing logic is tested rather than assumed.

`test_gui.py` builds the real widgets on Qt's offscreen platform, so it runs
headless and in CI. It covers construction and the data path from the image
table to a post payload - not appearance. It skips cleanly if PySide6 is absent.

No network access and no credentials needed for either suite.
