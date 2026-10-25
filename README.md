# SIP Door/Gate Opener — web UI, metrics & SIP diagnostics

A small Python service that opens doors/gates over a VoIP call. Each target
(GATE, DOOR, LAB, …) has its own phone number; pressing a button places a SIP
call that the opener device recognises by **caller-ID** and unlocks. The call
auto-hangs-up after a configurable timeout (default 10 s).

It is built around **PJSUA2 (PJSIP)** for SIP and **Flask** for the web UI, and
exposes rich **Prometheus metrics** plus full **SIP message tracing** so call
problems are diagnosable without guesswork.

---

## How the openers actually work (important)

Most GSM/VoIP openers **do not answer** the call — they ring, read the incoming
**caller-ID**, open, and never pick up. So the "success" path is:

```
INVITE → 100 Trying → 183/180 Ringing → (we auto-hang-up after N s) → 487
```

A `487 Request Terminated` here is **success**, not failure — it's the response
to our own CANCEL after the device rang. The real health signal is therefore
**"did the far end ring?"** (`gate_last_call_reached` / a provisional response),
*not* the final SIP code. See [Call outcomes](#call-outcomes).

---

## Architecture

```
          Browser (LAN / WireGuard)
                 │  HTTPS  gate.int.hamlab.lt
                 ▼
            traefik (TLS, *.int.hamlab.lt wildcard)        rootless Podman on fnuc
                 │  http  :5000
                 ▼
   ┌─────────────────────────────────────────┐
   │  atidarytuvas (Flask, this app)          │
   │   app.py        routes /call /status …   │
   │   sip_client.py PJSUA2 client + metrics  │
   │   logger.py     configurable logging     │
   └───────────────┬──────────────────────────┘
                   │  SIP/UDP :5060  (REGISTER + INVITE)
                   ▼
         peoplefone SIP trunk  (sips.peoplefone.lt → 185.190.125.3)
                   │
                   ▼
         gate / door / lab opener devices (ring-and-open by caller-ID)

   Prometheus ── scrapes ──► /metrics (job "gate")
   Grafana   ── "Door/Gate opener (SIP)" dashboard row
```

**Components**
- **Flask app** (`app.py`) — serves the UI, the `/call` / `/status` / `/targets`
  / `/lastcall` / `/metrics` endpoints. Single-threaded (`threaded=False`) because
  PJSIP requires calls from registered threads.
- **SIP client** (`sip_client.py`) — PJSUA2 `Endpoint` + `Account` (`SIPAccount`)
  + per-call `SIPCall`. Registers to the trunk, places calls, auto-hangs-up via a
  `threading.Timer` (the timer thread registers itself with PJSIP before calling
  `hangup()`). Holds the thread-safe `CallMetrics`.
- **Logger** (`logger.py`) — `AppLogger` with ERROR/INFO verbosity.

**Call flow**
1. `POST /call {"target":"GATE"}` → `make_call(number, target, source)`.
2. `INVITE` sent; a `hangup_timeout`-second timer starts **immediately** (not on
   answer), so the call tears down N s after dialling regardless of answer.
3. Provisional responses (`100/180/183`) → `reached=True`.
4. Timer fires → `CANCEL`/`BYE` → `DISCONNECTED`; the outcome is classified and
   recorded in metrics.

---

## Repository layout

```
app.py               Flask app + routes + target label/order helpers
sip_client.py        PJSUA2 SIP client, CallMetrics, classify_result()
logger.py            AppLogger (ERROR / INFO / DEBUG)
templates/index.html Web UI (buttons from /targets; polls /lastcall for result)
static/style.css     Styling
Containerfile        CentOS 9 image, PJSIP 2.16 built from source
conftest.py          Test bootstrap (stubs native pjsua2)
tests/               pytest suite (43 tests, no native lib needed)
requirements.txt     Runtime deps (Flask, python-dotenv)
requirements-dev.txt Test deps (pytest, flask, python-dotenv)
.env                 Secrets/config (gitignored)
```

---

## Setup (local)

```bash
sudo dnf install python3-pjsua2      # Fedora/RHEL  (apt-get on Debian/Ubuntu)
python3 -c "import pjsua2; print('ok')"
pip install -r requirements.txt
cp .env.example .env                  # edit credentials
python app.py                         # http://0.0.0.0:$FLASK_PORT (default 5000)
```

## Configuration (env / `.env`)

| Variable | Meaning |
|---|---|
| `SIP_PROXY` | SIP server FQDN, e.g. `sips.peoplefone.lt:5060` (no `sip:` prefix) |
| `SIP_USERNAME`, `SIP_PASSWORD` | SIP account credentials |
| `PHONE_NUMBER_<NAME>` | A target. Plain `+37061454944` → `sip:<num>@proxy`, or a full `sip:` URI used as-is. One button per target. |
| `PHONE_LABEL_<NAME>` | Friendly button label (defaults: GATE→"Parking Gate", DOOR→"Main Entrance", LAB→"Lab"; else title-cased id). |
| `PHONE_ORDER_<NAME>` | Integer button sort order (defaults GATE=1, DOOR=2, LAB=3, else 99). |
| `HANGUP_TIMEOUT` | Seconds after dialling before auto-hangup. Default 10; invalid/≤0 → 10. |
| `FLASK_PORT` | Web port (default 5000). |
| `FLASK_DEBUG` | `true`/`false` (default false). **Keep false in production** (Werkzeug debugger = RCE if reachable). |
| `LOG_LEVEL` | App verbosity: `ERROR` (default), `INFO`, or `DEBUG`/`HIGH` (= verbose). |
| `PJSIP_LOG_LEVEL` | PJSIP stack verbosity 0–5 (default **4** = full SIP message trace to stdout; 0 = silent). |
| `SIP_RESOLVE_PROXY` | `false` (default) uses the proxy FQDN + pjsip DNS (like a normal client). `true` resolves it to a raw IP first (legacy; can break outbound on SBCs that route by domain). |

Internal target **ids** (`<NAME>`) are what `/call` and the metrics key on; labels
are display-only, so buttons can be renamed without touching the openers.

---

## HTTP endpoints

- `GET /` — web UI.
- `GET /targets` — ordered `[{id, label}, …]`.
- `GET /status` — `{sip_client_initialized, registered, has_active_call, proxy}`.
- `POST /call` — body `{"target":"GATE"}`. `200` on dial, `409 {busy:true}` if a
  call is already active, `400` unknown/missing target. Records the presser's
  source IP (see [Source IP / audit](#source-ip--audit)).
- `GET /lastcall/<target>` — outcome of the last call to a target (`result`,
  `reached`, `status`, `first_response_code`, `ring_seconds`, `source`,
  `delivered`, `in_progress`, `hangup_timeout`). The UI polls this after dialling
  to show green/red.
- `GET /metrics` — Prometheus exposition.

---

## Metrics (`/metrics`)

Labelled by `target` so a broken target is distinguishable from a working one.

| Metric | Use |
|---|---|
| `gate_sip_registered` | 1/0 — account registered now |
| `gate_sip_registration_status_code` | last REGISTER status (200/401/…) |
| `gate_sip_registrations_total` / `gate_sip_register_failures_total` | registration churn |
| `gate_sip_last_register_ok_timestamp_seconds` | last successful REGISTER |
| `gate_call_attempts_total{target}` | INVITEs sent |
| `gate_call_provisional_total{target,code}` | **100/180/183 received** — proves the trunk answered the INVITE |
| `gate_last_call_first_response_code{target}` | first provisional code (0 = trunk never replied) |
| `gate_last_call_ring_seconds{target}` | **post-dial delay** (~1 s = answered, ~10 s = timeout / no response) |
| `gate_last_call_reached{target}` | 1 if a provisional/answer was seen |
| `gate_call_result_total{target,result}` / `gate_last_call_result{target,result}` | outcome counter + current state |
| `gate_last_sip_status_code{target}` / `gate_call_final_status_total{target,code}` | final disconnect code + distribution |
| `gate_last_call_duration_seconds` / `gate_last_call_timestamp_seconds` | per-call timing |
| `gate_call_source_total{target,source}` / `gate_last_call_source{target,source}` | presser IP (see below) |
| `gate_call_in_progress` | 1 while a call is active |
| `gate_build_info{version,pjsip,proxy,username}` | constant 1 |

Metrics are in-process (reset on restart); history lives in Prometheus.

### Call outcomes

`classify_result(answered, we_hung_up, reached, last_status)` (pure, unit-tested):

- **answered** — callee answered (200/CONFIRMED).
- **cancelled** — far end rang (got a provisional) then we auto-hung-up: the
  **normal ring-and-open success**. `reached=1`, a 487 here is expected.
- **no_response** — we auto-hung-up but **no provisional ever arrived**: the INVITE
  wasn't delivered (`reached=0`, first_response_code 0). See the MTU finding below.
- **rejected** — provider/callee ended it with a 4xx/5xx/6xx we didn't cause
  (e.g. `486 Busy Here` when the callee declines).
- **ended** — any other clean teardown.

The web UI colours the status green for a delivered call (`cancelled`/`answered`)
and red for `no_response`/`rejected`, showing the SIP code.

---

## Deployment (HamLab / Podman)

Runs as a **rootless Podman Quadlet** on the services host `fnuc`, behind traefik
at `gate.int.hamlab.lt`, managed by the Ansible role `fnuc_quadlets`.

### Image (`Containerfile`)

CentOS Stream 9 base, **PJSIP 2.16 built from source** (configure → make → swig
Python bindings → `python3 setup.py install`), then the app code, running as a
non-root user `hamlab` (UID 1000). Build is ~8 min (PJSIP compile); the app-code
`COPY` is the last layer so code changes rebuild fast.

```bash
cd ~/git/atidarytuvas
podman build -f Containerfile -t localhost/atidarytuvas:hangup .
```

Note: the swig install step must use `python3` (CentOS has no bare `python`), and
the base is pinned to `centos:9`.

### Quadlet unit (Ansible `fnuc_quadlets`)

Key points of the `atidarytuvas` service spec:
- `image: localhost/atidarytuvas:hangup`, `pull: never` (locally built).
- `network: traefik`; traefik labels route `gate.int.hamlab.lt` → `:5000` (TLS via
  the `*.int.hamlab.lt` wildcard cert).
- **Secrets** (`SIP_PASSWORD`, phone numbers, `SECRET_KEY`) live in a 600
  `EnvironmentFile` on fnuc (`~/.config/containers/systemd/atidarytuvas.env`), not
  in the repo.
- **Dev loop:** the app source (`app.py`, `sip_client.py`, `logger.py`,
  `templates/`) is **bind-mounted from `~/git/atidarytuvas` over the image**, so a
  code change is just `git` edit + `systemctl --user restart atidarytuvas.service`
  — no 8-min rebuild.

### Monitoring

- Prometheus job `gate` scrapes `http://atidarytuvas:5000/metrics` every 30 s
  (internal, on the traefik network).
- Grafana dashboard `hamlab-net` has a **"Door/Gate opener (SIP)"** row:
  registration, post-dial delay, reached, and call outcomes.

---

## Findings & troubleshooting

### ⭐ Root cause of "calls silently fail": oversized INVITE vs path MTU

**Symptom:** REGISTER succeeds and the account shows registered, but outbound
calls get **no SIP response at all** — no `100`, no `180`, not even a reject —
so every call ends `no_response` (10 s timeout → local 487). Intermittent-looking.

**Cause:** PJSIP's default SDP offers ~11 audio codecs **plus a text media line**,
making the `INVITE` ~**1458 bytes**. The route to the trunk is **tunnelled with
PMTU 1420**:

```bash
ip route get 185.190.125.3        # → … mtu 1420
ping -M do -s 1400 185.190.125.3  # → fails (frag needed)
```

A ~1458-byte UDP datagram (IP ~1486 B, DF set via PMTUD) **exceeds 1420 and is
dropped before leaving the host** — while small REGISTER/OPTIONS (~500 B) pass.
So the trunk never even receives the INVITE. (`tcpdump` confirms the INVITE never
egresses; once it fits, the trunk's `407 Proxy Authentication Required` appears —
previously unseen because the INVITE never arrived.)

**Fix:** shrink the INVITE below the PMTU by limiting codecs to **PCMU only** at
startup (the opener uses a null audio device, so media doesn't matter):

```python
for ci in endpoint.codecEnum2():
    endpoint.codecSetPriority(ci.codecId, 255 if ci.codecId.startswith("PCMU/") else 0)
```

Result: INVITE ~1159 B (1374 B after the Proxy-Authorization header) ≪ 1420 →
delivered → `407` → `100`/`183`/`180` → rings. **If the SDP ever grows again,
switch the SIP transport to TCP** (`PJSIP_TRANSPORT_TCP`) — TCP segments, so UDP
fragmentation/PMTU no longer applies.

### SIP debug playbook

- **Turn on tracing:** `PJSIP_LOG_LEVEL=4` (full SIP messages to the journal) and
  `LOG_LEVEL=INFO` (app-level per-call events). Then read:
  `journalctl --user -u atidarytuvas.service -f`.
- **Interpret a call:** look for `[TARGET] state=… sip=…` lines and
  `1st res in N ms`. `~1 s` = the trunk answered; `~10 s` with `NO RESPONSE`
  = the INVITE isn't being delivered (think MTU / routing / trunk block).
- **Is it the packet or the trunk?** Capture on the host:
  `sudo tcpdump -ni any 'udp and host <proxy-ip>'` during a call. If the INVITE
  never appears outbound → local/MTU drop. If it egresses but nothing returns →
  trunk/NAT.
- **Check PMTU:** `ip route get <proxy-ip>` and `ping -M do -s <size> <proxy-ip>`.
- **FQDN vs IP:** keep `SIP_RESOLVE_PROXY=false` so SIP URIs use the proxy FQDN;
  some SBCs route outbound by the From/Request-URI domain and silently drop IPs.

### Things that looked like the cause but weren't

A shared SIP account (several devices registered on `90852528635`), a per-account
channel/quota limit (`4/5 išnaudota`), the Lithuanian `8`→`0` dialling change, a
caller-ID whitelist, and trunk balance were all investigated and **ruled out** —
the only cause was the INVITE MTU drop. (A dedicated SIP sub-account per opener is
still good hygiene, but it was not the fault.)

### Source IP / audit

`/call` records the presser's IP from `X-Forwarded-For` (`gate_call_source_total`
/ `gate_last_call_source`, and `source` in `/lastcall`). **Caveat:** under rootless
Podman the inbound connection is SNAT'd to the Podman bridge gateway
(`10.89.2.x`) before traefik, so the value is the ingress gateway, **not** the real
client/VPN IP. Recovering the true client IP needs traefik on host/pasta
networking or a dedicated source-preserving exposure (not done — invasive for a
shared ingress). For reliable per-person audit, prefer authentication (the UI has
none by default).

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest        # 43 tests
```

`conftest.py` stubs the native `pjsua2` module, so the suite runs without the
compiled PJSIP bindings. Coverage: `classify_result`, `_esc`, `_state_name`,
`CallMetrics`, `/metrics` exposition, and the Flask routes + target label/order
helpers (with a fake SIP client). Prefer extracting pure functions (like
`classify_result`) so logic stays testable without PJSIP.

---

## Security notes

- Never commit `.env` (SIP credentials) — it is gitignored.
- Keep `FLASK_DEBUG=false` in production.
- The web UI has **no authentication** by default — add it (or front it with
  traefik auth) before any exposure, and for per-person audit.

## License

Provided as-is for educational and personal use.
