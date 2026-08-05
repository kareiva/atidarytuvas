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
- **GET /targets**: List of phone number targets
- **POST /call**: `{"target": "DOOR"}`

## Configuration

Environment variables (`.env` file):

- **SIP_PROXY**: Server domain (no `sip:` prefix)
- **SIP_USERNAME**, **SIP_PASSWORD**: Credentials
- **PHONE_NUMBER_<NAME>**: `PHONE_NUMBER_DOOR=+1234567890`
  - Plain: `+1234567890` → `sip:+1234567890@proxy`
  - Full URI: `sip:+1234567890@domain.com` (used as-is)
- **FLASK_PORT**: Default 5000
- **FLASK_DEBUG**: `true`/`false`, default false
- **LOG_LEVEL**: ERROR (default), INFO
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

Default is ERROR (errors only). Set LOG_LEVEL=INFO for verbose output.

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
