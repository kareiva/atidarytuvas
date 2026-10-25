# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GSM Door Opener - Flask web application using SIP/VoIP to trigger multiple GSM door opener devices. Web interface displays buttons for each configured target (DOOR, GATE, GARAGE). Clicking a button makes a SIP call that unlocks the device. Calls auto-hangup after a configurable timeout (default 10s).

## Critical Architecture

### PJSUA2 (PJSIP) Library Constraints

- **System package only**: Cannot be installed via pip. Use system package (e.g., `python3-pjsua2`)
- **Thread safety**: Flask runs single-threaded (`threaded=False`) for PJSIP compatibility
- **Event-driven callbacks**: Uses PJSUA2 callback classes (`Account`, `Call`) for async handling
- **Cleanup order**: hangup timer → active call → account → endpoint

### Component Architecture

```
Flask (app.py) → AppLogger (logger.py) → SIP Client (sip_client.py)
                                          ├── SIPAccount (registration)
                                          ├── SIPCall (call states)
                                          └── Thread-safe (Lock)
```

### Call Flow

1. GET /targets → Fetch configured targets
2. POST /call `{"target": "DOOR"}` → Initiate call
3. INVITE → 100 Trying → 180 Ringing → 200 OK → HANGUP_TIMEOUT timer → BYE

Note: `make_call` starts the auto-hangup timer as soon as the INVITE is sent (not on answer), so the call is torn down `HANGUP_TIMEOUT` seconds after dialing regardless of whether it is answered.

### API Endpoints

- **GET /**: Web interface
- **GET /status**: SIP registration status
- **GET /targets**: ordered `[{id, label}, …]` (friendly labels; see below)
- **POST /call**: `{"target": "DOOR"}` (target = the id, not the label)
- **GET /metrics**: Prometheus exposition (see Metrics & Observability)

## Configuration

Environment variables (`.env` file):

- **SIP_PROXY**: Server domain (no `sip:` prefix)
- **SIP_USERNAME**, **SIP_PASSWORD**: Credentials
- **PHONE_NUMBER_<NAME>**: `PHONE_NUMBER_DOOR=+1234567890`
  - Plain: `+1234567890` → `sip:+1234567890@proxy`
  - Full URI: `sip:+1234567890@domain.com` (used as-is)
- **PHONE_LABEL_<NAME>**: friendly button label (defaults GATE→"Parking Gate", DOOR→"Main Entrance", LAB→"Lab"; else title-cased id)
- **PHONE_ORDER_<NAME>**: integer button sort order (defaults GATE=1, DOOR=2, LAB=3, else 99)
- **FLASK_PORT**: Default 5000
- **FLASK_DEBUG**: `true`/`false`, default false (keep false in prod — enables the Werkzeug debugger)
- **LOG_LEVEL**: app verbosity — ERROR (default), INFO, or DEBUG/HIGH (= verbose, same as INFO). DEBUG/HIGH are mapped to INFO; an unrecognised value falls back to ERROR.
- **PJSIP_LOG_LEVEL**: PJSIP stack verbosity 0–5 (default 4 = full SIP message trace to stdout; 0 = silent)
- **HANGUP_TIMEOUT**: Seconds after dialing before auto-hangup. Default 10. Non-positive/invalid values fall back to 10.

## Development

**Local setup:**
```bash
sudo dnf install python3-pjsua2  # Or apt-get on Debian/Ubuntu
pip install -r requirements.txt
cp .env.example .env  # Edit with credentials
python app.py
```

**Container:**
```bash
podman build -t gsm-door-opener -f Containerfile .
podman run -d -p 5000:5000 -v $(pwd)/.env:/app/.env:ro gsm-door-opener
```

## Critical Implementation Details

### Thread Safety & Auto-Hangup

**PJSIP Threading Requirement**: All PJSIP functions must be called from PJSIP-registered threads.

**Auto-hangup mechanism** (see `_post_hangup_request()` in sip_client.py):
1. `make_call` sends INVITE → starts a `threading.Timer(hangup_timeout, _post_hangup_request)`
2. Timer fires on its own thread → `_post_hangup_request` registers that thread with PJSIP via `pj.Endpoint.instance().libRegisterThread(...)`, then calls `call.hangup()` directly (guarded by a state check that it isn't already `DISCONNECTED`)

The timer thread calls PJSIP directly — it is safe *because* it registers itself first. This is the required pattern for any code that touches PJSIP off the main/callback threads; do not call PJSIP from an unregistered thread.

**Call teardown paths, both cancel the timer under the lock:**
- Normal/failed disconnect → `SIPCall.onCallState` (DISCONNECTED) → `_clear_call()`
- Timeout → `_post_hangup_request()`

**Lock protection:** `self.call_lock` protects `current_call` and `hangup_timer`. Always acquire before reading/modifying either.

### Logging

AppLogger class with two verbosity levels:
- `error()` - Always shown
- `info()` - INFO level only

Default is ERROR (errors only). Set LOG_LEVEL=INFO (or DEBUG/HIGH) for verbose
app output. SIP message tracing is separate: `PJSIP_LOG_LEVEL` (default 4) prints
the raw SIP dialog to stdout — invaluable for trunk debugging.

## Code Modification Guidelines

### Adding call targets

Add `PHONE_NUMBER_<NAME>=+number` to .env → Restart → UI auto-generates button

### Adding SIP functionality

- Extend `SIPCall` or `SIPAccount` in sip_client.py
- Use PJSUA2 callbacks (`onCallState`, `onRegState`)
- **NEVER call PJSIP from an unregistered thread** — call `libRegisterThread()` first (see `_post_hangup_request`)
- Audio is disabled: `audDevManager().setNullDev()` is called at startup so no sound card is needed (door openers only need the call to connect, not media)
- Handle exceptions (PJSIP crashes on unhandled exceptions)

### Modifying Flask endpoints

- Keep single-threaded (PJSIP constraint)
- Use global `sip_client` instance — it is lazily created on the first request via `@app.before_request` (and re-attempted inside `/call` if still `None`), not at import time
- Return `{"success": bool, "message": str}`

### Adding logging

- Use AppLogger methods, not standard `logging`
- `logger.error()` for errors (always shown)
- `logger.info()` for informational messages (INFO level only)
- Example: `self.logger.info("Call initiated")` not `logger.info("Call initiated")`

## Security

- Container runs as non-root user (hamlab, UID 1000)
- Never commit `.env` file
- No authentication on web interface (add for public deployment)

## Metrics & Observability

`GET /metrics` returns Prometheus text (built by `SIPClient.render_metrics()`;
backed by the thread-safe `CallMetrics` class). Everything is labelled by
`target` so a broken target is distinguishable from a working one.

Key series: `gate_sip_registered`, `gate_sip_registration_status_code`,
`gate_sip_registrations_total` / `_register_failures_total`,
`gate_call_attempts_total{target}`, `gate_call_provisional_total{target,code}`
(100/180/183 — proves the trunk answered the INVITE),
`gate_last_call_first_response_code{target}` (0 = never answered),
`gate_last_call_ring_seconds{target}` (post-dial delay: ~1s answered vs ~10s
timeout), `gate_last_call_reached{target}`,
`gate_call_result_total{target,result}` + `gate_last_call_result{target,result}`,
`gate_last_sip_status_code{target}` + `gate_call_final_status_total{target,code}`,
`gate_call_in_progress`, `gate_build_info`.

### Result classification (`classify_result()` — pure, unit-tested)

- **answered** — callee answered (200/CONFIRMED).
- **cancelled** — far end rang (got a provisional) then we auto-hung-up: the
  normal "ring-and-open" path (`reached=1`; a 487 here is SUCCESS, not failure).
- **no_response** — we auto-hung-up but NO provisional ever arrived: the trunk
  silently dropped the INVITE (`reached=0`, first_response_code 0). Typical of no
  outbound balance / outbound barred.
- **rejected** — provider/callee ended it with a 4xx/5xx/6xx we did not cause.
- **ended** — any other clean teardown.

**Gotcha:** many openers never send a SIP answer — they ring and open on caller-ID,
so `cancelled`+487 is normal. The real health signal is `reached` /
`first_response_code`, NOT the final code. `no_response` + `ring_seconds ≈ 10s`
means the call isn't leaving the trunk (check outbound balance/permission), not an
app bug.

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest
```

`conftest.py` stubs the native `pjsua2` module, so the suite runs without the
compiled PJSIP bindings. `tests/test_sip_client.py` covers `classify_result`,
`_esc`, `_state_name`, `CallMetrics`, and `render_metrics` output;
`tests/test_app.py` covers the target label/order helpers and the Flask routes
with a fake SIP client (skipped if flask/dotenv aren't installed). Prefer
extracting pure functions (like `classify_result`) so logic is testable without
PJSIP.

## HamLab deployment note

Runs as a Podman Quadlet (`atidarytuvas`) behind traefik at
`gate.int.hamlab.lt`, managed by the `fnuc_quadlets` Ansible role. The image
(`localhost/atidarytuvas:hangup`, CentOS 9 + PJSIP from source, ~8-min build) has
the code baked in, but `app.py`/`sip_client.py`/`logger.py`/`templates` are also
**bind-mounted from `~/git/atidarytuvas` over the image**, so a code change is just
`git` edit + `systemctl --user restart atidarytuvas.service` — no rebuild. Secrets
(SIP creds) live in a 600 `EnvironmentFile`, not the repo. `/metrics` is scraped by
the fnuc Prometheus (job `gate`, 30s), internal only.

## Critical gotcha: INVITE size vs path MTU

If outbound calls silently fail (REGISTER OK, but `no_response` — no 100/180/183,
`1st res in ~10000 ms`), suspect the **INVITE exceeding the path MTU**. The route
to the trunk here is tunnelled (PMTU 1420: `ip route get <proxy-ip>`), and PJSIP's
default SDP (~11 codecs + a text m-line) makes the INVITE ~1458 B, which is dropped
(DF) before egress. Fix = limit codecs to PCMU only at startup (see `sip_client.py`,
`codecSetPriority`); INVITE drops to ~1159 B (~1374 B authed) < 1420. Verify with
`tcpdump -ni any 'udp and host <proxy-ip>'` (the INVITE won't egress until it fits)
and `ping -M do -s <size> <proxy-ip>`. Bulletproof alternative: SIP over TCP. Full
write-up + debug playbook in README.md ("Findings & troubleshooting").
