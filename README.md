# SIP Door/Gate Opener — web UI + metrics

A lightweight Python SIP client with a Flask web interface for opening doors/gates
over a VoIP call. Each target (e.g. GATE, DOOR, LAB) has its own phone number; a
button press places a SIP call that the opener device recognises by caller‑ID and
unlocks. The call auto‑hangs‑up after a configurable timeout (default 10 s).

## Features

- Simple web UI with one button per target, **friendly labels + custom order**.
- **PJSUA2 (PJSIP)** — production‑grade SIP stack.
- Auto hang‑up N seconds after dialling (typical "ring‑and‑open" behaviour).
- **Prometheus `/metrics`** — registration health + per‑target call outcomes,
  provisional responses, post‑dial delay, final SIP codes. Makes "did the call
  actually reach the far end?" observable without reading logs.
- **Full SIP message tracing** to stdout (`PJSIP_LOG_LEVEL`) for debugging trunks.
- Thread‑safe call handling; config via `.env` / environment.
- Unit tests (pytest) that run without the native PJSIP library.

## Project structure

```
app.py              # Flask app: routes /, /call, /status, /targets, /metrics
sip_client.py       # PJSUA2 SIP client, CallMetrics, classify_result()
logger.py           # Configurable AppLogger (ERROR / INFO / DEBUG)
templates/index.html# Web interface (buttons built from /targets)
static/style.css    # Styling
Containerfile       # CentOS 9 + PJSIP built from source
conftest.py         # Test bootstrap (stubs native pjsua2)
tests/              # pytest suite
requirements.txt    # Runtime deps (Flask, python-dotenv)
requirements-dev.txt# Test deps (pytest, flask, python-dotenv)
.env                # Secrets/config (gitignored)
```

## Setup

### 1. Install PJSUA2 (system package — not pip)

```bash
sudo dnf install python3-pjsua2       # Fedora/RHEL
sudo apt-get install python3-pjsua2   # Debian/Ubuntu
python3 -c "import pjsua2; print('ok')"
```

### 2. Python deps

```bash
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env   # then edit
```

### 4. Run

```bash
python app.py          # serves on http://0.0.0.0:$FLASK_PORT (default 5000)
```

## Configuration (env / `.env`)

| Variable | Meaning |
|---|---|
| `SIP_PROXY` | SIP server, e.g. `sips.example.com:5060` (no `sip:` prefix) |
| `SIP_USERNAME`, `SIP_PASSWORD` | SIP account credentials |
| `PHONE_NUMBER_<NAME>` | A target. Plain `+37061454944` → `sip:<num>@proxy`, or a full `sip:` URI used as‑is. One button per target. |
| `PHONE_LABEL_<NAME>` | Friendly button label (defaults: GATE→"Parking Gate", DOOR→"Main Entrance", LAB→"Lab"; otherwise title‑cased name). |
| `PHONE_ORDER_<NAME>` | Integer sort order for the buttons (defaults GATE=1, DOOR=2, LAB=3, else 99). |
| `HANGUP_TIMEOUT` | Seconds after dialling before auto‑hangup. Default 10; invalid/≤0 → 10. |
| `FLASK_PORT` | Web port (default 5000). |
| `FLASK_DEBUG` | `true`/`false` (default false). **Keep false in production** — Flask debug enables the Werkzeug debugger. |
| `LOG_LEVEL` | App log verbosity: `ERROR` (default), `INFO`, or `DEBUG`/`HIGH` (= verbose, same as INFO). |
| `PJSIP_LOG_LEVEL` | PJSIP stack verbosity 0–5 (default **4** = full SIP message trace to stdout). Set `0` to silence. |

Internal target **ids** (`<NAME>`) are what `/call` and the metrics key on; labels
are display‑only, so you can rename buttons without touching the openers.

## HTTP endpoints

- `GET /` — web interface.
- `GET /targets` — ordered `[{id, label}, …]`.
- `GET /status` — `{sip_client_initialized, registered, has_active_call, proxy}`.
- `POST /call` — body `{"target": "GATE"}` → places the call.
- `GET /metrics` — Prometheus exposition (below).

## Metrics (`/metrics`)

Everything is labelled by `target` so a broken target is distinguishable from a
working one.

| Metric | Use |
|---|---|
| `gate_sip_registered` | 1/0 — account registered now |
| `gate_sip_registration_status_code` | last REGISTER status (200/401/…) |
| `gate_sip_registrations_total` / `gate_sip_register_failures_total` | registration churn |
| `gate_sip_last_register_ok_timestamp_seconds` | last successful REGISTER |
| `gate_call_attempts_total{target}` | INVITEs sent |
| `gate_call_provisional_total{target,code}` | **100/180/183 received** — proves the trunk answered the INVITE |
| `gate_last_call_first_response_code{target}` | first provisional code (0 = trunk never replied) |
| `gate_last_call_ring_seconds{target}` | **post‑dial delay**: INVITE→first provisional (~1 s = answered, ~10 s = timeout) |
| `gate_last_call_reached{target}` | 1 if a provisional/answer was seen |
| `gate_call_result_total{target,result}` / `gate_last_call_result{target,result}` | outcome counter + current state |
| `gate_last_sip_status_code{target}` / `gate_call_final_status_total{target,code}` | final disconnect code + distribution |
| `gate_last_call_duration_seconds` / `gate_last_call_timestamp_seconds` | per‑call timing |
| `gate_call_in_progress` | 1 while a call is active |
| `gate_build_info{version,pjsip,proxy,username}` | constant 1 |

### Call outcomes (`result`)

`classify_result()` labels each finished call:

- **answered** — the callee answered (200/CONFIRMED).
- **cancelled** — the far end rang (got a provisional) and we auto‑hung‑up: the
  normal "ring‑and‑open" path. `reached=1`.
- **no_response** — we auto‑hung‑up but **no provisional ever arrived**: the trunk
  silently dropped the INVITE (not delivered). `reached=0`, first_response_code 0.
  Typical of **no outbound balance / outbound barred**.
- **rejected** — the provider/callee ended it with a 4xx/5xx/6xx we did not cause.
- **ended** — any other clean teardown.

> Note: many GSM/VoIP openers never send a SIP answer — they just ring and open on
> caller‑ID. So `cancelled` with a 487 (our CANCEL) is a **success**, not a failure.
> The real health signal is `gate_last_call_reached` / `first_response_code`:
> `no_response` + `ring_seconds ≈ 10 s` means the call isn't leaving the trunk.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Tests stub the native `pjsua2` module (`conftest.py`), so they run anywhere
without the compiled PJSIP bindings. They cover `classify_result`, the metrics
(`CallMetrics`, `/metrics` exposition), and the Flask routes + target label/order
helpers.

## Security notes

- Never commit `.env` (it holds SIP credentials) — it is gitignored.
- Keep `FLASK_DEBUG=false` in production.
- Consider authentication in front of the web UI for production.

## License

Provided as‑is for educational and personal use.
