# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GSM Door Opener - Flask web application using SIP/VoIP to trigger multiple GSM door opener devices. Web interface displays buttons for each configured target (DOOR, GATE, GARAGE). Clicking a button makes a SIP call that unlocks the device. Calls auto-hangup after 10 seconds.

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
3. INVITE → 100 Trying → 180 Ringing → 200 OK → 10s timer → BYE

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
- **LOG_LEVEL**: ERROR (default), INFO

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

**Queue-based hangup mechanism:**
1. Call answered → Start 10s `threading.Timer`
2. Timer fires → Posts "HANGUP" to queue (no PJSIP calls)
3. PJSIP callback fires → Processes queue, calls `hangup()` safely

See `_post_hangup_request()` and `_process_hangup_queue()` in sip_client.py.

**Lock protection:** `self.call_lock` protects `current_call` and `hangup_timer`. Always acquire before modifying.

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
- **NEVER call PJSIP from external threads** - use queue pattern
- Handle exceptions (PJSIP crashes on unhandled exceptions)

### Modifying Flask endpoints

- Keep single-threaded (PJSIP constraint)
- Use global `sip_client` instance
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
