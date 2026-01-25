# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GSM Door Opener - A Flask web application that uses SIP/VoIP to trigger multiple GSM door opener devices. The web interface displays buttons for each configured target (e.g., DOOR, GATE, GARAGE). When a button is clicked, the application makes a SIP call to the corresponding phone number, which causes the device to unlock. The call automatically hangs up after 10 seconds.

## Critical Architecture Principles

### PJSUA2 (PJSIP) Library Usage

This application uses **PJSUA2**, the modern Python bindings for PJSIP. Key constraints:

- **System package installation only**: PJSUA2 cannot be installed via pip. It must be installed as a system package (e.g., `python3-pjsua2` on Fedora/Ubuntu).
- **Thread safety**: PJSIP has strict threading requirements. Flask runs in single-threaded mode (`threaded=False`) to avoid PJSIP thread registration issues.
- **Event-driven callbacks**: The SIP client uses PJSUA2's callback classes (`Account`, `Call`) to handle registration and call state changes asynchronously.
- **Resource cleanup**: Proper cleanup order is critical: hangup timer → active call → account → endpoint. See `SIPClient.stop()` in sip_client.py:326.

### Component Architecture

```
Flask Web Server (app.py)
    ↓
    Initializes on first request (before_request hook)
    ↓
    Uses AppLogger (logger.py) for configurable verbosity
    ↓
SIP Client (sip_client.py)
    ├── Receives AppLogger instance
    ├── SIPAccount (handles registration callbacks)
    │   └── onRegState() - monitors registration status
    ├── SIPCall (handles call state callbacks)
    │   ├── onCallState() - tracks call progression (100, 180, 200 codes)
    │   └── Auto-hangup timer (10 seconds after call answered)
    └── Thread-safe call management (threading.Lock)

Logger (logger.py)
    ├── AppLogger class with verbosity control
    ├── LogLevel enum (CRITICAL=0, LOW=1, MEDIUM=2, HIGH=3)
    ├── Default: CRITICAL (minimal output - production ready)
    └── Specialized methods (sip_request, sip_response, call_event, etc.)
```

### Call Flow State Machine

1. **Page loads** → GET /targets (fetches list of configured targets)
2. **UI renders** → Creates a button for each target (DOOR, GATE, etc.)
3. **User clicks button** → POST /call with `{"target": "DOOR"}`
4. **INVITE sent** → sip:+number@proxy (sip_client.py:254)
5. **100 Trying** ← Proxy received request
6. **180 Ringing** ← Door opener phone is ringing
7. **200 OK** ← Call answered (triggers 10-second timer)
8. **Auto-hangup** → BYE sent after 10 seconds (sip_client.py:316)
9. **Call ends** → Timer cancelled, call cleared

The timer is only started when the call reaches CONFIRMED state (200 OK received). If the call is not answered or fails, no timer is created.

### API Endpoints

- **GET /**: Serves the web interface
- **GET /status**: Returns SIP client registration status
- **GET /targets**: Returns list of configured phone number targets
- **POST /call**: Initiates a call to the specified target
  - Body: `{"target": "DOOR"}` where target matches a configured PHONE_NUMBER_<NAME>

## Development Commands

### Local Development

**Prerequisites:**
```bash
# Install PJSUA2 (system package - REQUIRED)
sudo dnf install python3-pjsua2  # Fedora/RHEL
# OR
sudo apt-get install python3-pjsua2  # Debian/Ubuntu

# Verify PJSUA2 installation
python3 -c "import pjsua2; print('✓ pjsua2 installed')"
```

**Setup:**
```bash
# Install Python dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your SIP credentials
```

**Run application:**
```bash
python app.py
# Access at http://localhost:5000
```

### Container Development

**Build container image:**
```bash
podman build -t gsm-door-opener -f Containerfile .
```

**Run container with .env file:**
```bash
podman run -d \
  --name door-opener \
  -p 5000:5000 \
  -v $(pwd)/.env:/app/.env:ro \
  gsm-door-opener
```

**Run container with environment variables:**
```bash
podman run -d \
  --name door-opener \
  -p 5000:5000 \
  -e SIP_PROXY=sip.example.com \
  -e SIP_USERNAME=user \
  -e SIP_PASSWORD=pass \
  -e PHONE_NUMBER_DOOR=+1234567890 \
  -e PHONE_NUMBER_GATE=+0987654321 \
  gsm-door-opener
```

**View logs:**
```bash
podman logs -f door-opener
```

**Cleanup:**
```bash
podman stop door-opener && podman rm door-opener
```

### Testing PJSIP Installation

```bash
# Verify PJSUA2 is available
python3 -c "import pjsua2; print('✓ PJSUA2 version:', pjsua2.Endpoint.version())"
```

## Configuration

All configuration is via `.env` file or environment variables:

- **SIP_PROXY**: SIP server domain (e.g., `sip.example.com` - no `sip:` prefix)
- **SIP_USERNAME**: SIP account username
- **SIP_PASSWORD**: SIP account password
- **PHONE_NUMBER_<NAME>**: Multiple phone numbers with named targets
  - Format: `PHONE_NUMBER_DOOR=+1234567890`
  - Examples:
    - `PHONE_NUMBER_DOOR=+1234567890`
    - `PHONE_NUMBER_GATE=+0987654321`
    - `PHONE_NUMBER_GARAGE=+1122334455`
  - Plain number: `+1234567890` → auto-formatted to `sip:+1234567890@proxy`
  - Full SIP URI: `sip:+1234567890@custom.com` → used as-is
  - The web interface creates a button for each configured target
- **FLASK_PORT**: Web interface port (default: 5000)
- **FLASK_DEBUG**: Flask debug mode (default: False)
- **LOG_LEVEL**: Logging verbosity (default: CRITICAL if not set)
  - **CRITICAL**: Startup, shutdown, and critical errors only (minimal output)
  - **LOW**: Essential events (registration, call start/end, errors)
  - **MEDIUM**: Include SIP response codes (100, 180, 200) and state changes
  - **HIGH**: Full SIP protocol details and debugging information

## Critical Implementation Details

### Signal Handling and Shutdown

The application has careful signal handling for clean shutdown (app.py:139-157):
- SIGINT/SIGTERM handlers registered
- Single Ctrl+C cleanly stops application
- Proper cleanup order: cancel timer → hangup call → destroy account → destroy endpoint

**Never modify the shutdown sequence without testing thoroughly.**

### Thread Safety

SIP call state is protected by `self.call_lock` (threading.Lock) in sip_client.py:132. This lock protects:
- `self.current_call` (prevents concurrent calls)
- `self.hangup_timer` (prevents race conditions)

**Always acquire the lock before checking or modifying call state.**

### Logging System

The application uses a centralized logging system (logger.py) with configurable verbosity:

**AppLogger class** - Provides methods for different message types:
- `critical()` - Always shown (startup, shutdown, critical errors)
- `essential()` - LOW+ (registration, call events, errors)
- `info()` - MEDIUM+ (SIP responses, state changes)
- `debug()` - HIGH only (full debug output)
- `error()` - Always shown

**Verbosity levels:**
- **CRITICAL** (default): Minimal output - startup, shutdown, errors only
- **LOW**: Essential events - registration, call start/end, important responses
- **MEDIUM**: Includes all SIP response codes (100, 180, 200) and state changes
- **HIGH**: Full SIP protocol details, tracebacks, and debugging

**Default behavior (no LOG_LEVEL set):**
- Only shows application startup/shutdown banners
- Critical errors
- No SIP registration details, no call flow logging
- Ideal for production where you only want to know if something breaks

**Logging format:**
The application uses structured logging with visual indicators:
- `──►` Outgoing SIP request (INVITE, BYE, REGISTER)
- `◄──` Incoming SIP response (100, 180, 200, etc.)
- `⏱` Timer events
- `✓` Success indicators
- `✗` Error indicators
- `════` Major section separators
- `────` Response separators

**Maintain this format when adding new logs** - it's specifically designed for troubleshooting SIP call flows.

**Logger instances:**
- `app.py` creates logger with name 'app'
- `sip_client.py` receives logger with name 'sip_client'
- Both share the same verbosity level from LOG_LEVEL environment variable

## Common SIP Response Codes

Understanding these is essential for debugging:

- **100 Trying**: Proxy received INVITE, processing
- **180 Ringing**: Destination phone is ringing
- **183 Session Progress**: Call setup in progress
- **200 OK**: Call answered (triggers auto-hangup timer)
- **486 Busy Here**: Line is busy
- **487 Request Terminated**: Call cancelled before answer
- **603 Decline**: Call rejected by device

## Troubleshooting Call Issues

### Call doesn't connect

1. Check logs for exact SIP response code in `[SIP-RESPONSE]` messages
2. Verify phone number format (E.164 recommended: `+1234567890`)
3. Ensure SIP account has outbound calling permissions
4. Check firewall allows UDP port 5060 (SIP) and 10000-20000 (RTP)

### Registration fails

1. Check `[REGISTRATION]` logs for error details
2. Verify SIP_PROXY has no `sip:` prefix in .env
3. Confirm username/password are correct
4. Ensure network allows UDP port 5060

### Container-specific issues

1. Verify PJSIP built correctly: `podman run --rm gsm-door-opener python3 -c "import pjsua2; print('✓')"`
2. Check environment variables are passed: `podman exec door-opener env | grep SIP`
3. Review build logs if image fails to build
4. Ensure .env file is mounted: `podman exec door-opener ls -la /app/.env`

## Code Modification Guidelines

### Adding new call targets

To add a new target (e.g., GARAGE, LOBBY):
1. Add environment variable: `PHONE_NUMBER_GARAGE=+1122334455` in .env
2. Restart the application
3. The UI will automatically create a button for the new target
4. No code changes needed - targets are loaded dynamically

### Adding new SIP functionality

- Extend `SIPCall` or `SIPAccount` classes in sip_client.py
- Use PJSUA2 callback methods (e.g., `onCallState`, `onRegState`)
- Always handle exceptions in callbacks (PJSIP can crash if callbacks throw)
- See PJSIP documentation: https://docs.pjsip.org/en/latest/api/pjsua2.html

### Modifying Flask endpoints

- Flask runs in single-threaded mode due to PJSIP constraints
- Do not enable `threaded=True` or use threading in Flask routes
- All SIP operations must use the global `sip_client` instance
- Return JSON responses with `success` and `message` fields

### Adding logging

When adding new logging statements:
- Use `logger.critical()` for startup/shutdown and critical errors (always shown)
- Use `logger.essential()` for important events (shown at LOW+)
- Use `logger.info()` for medium-verbosity messages (SIP responses, state changes - MEDIUM+)
- Use `logger.debug()` for high-verbosity debug output (HIGH only)
- Use `logger.error()` for errors (always shown)
- Use specialized methods: `sip_request()`, `sip_response()`, `call_event()`, etc.
- Do not use standard `logging.info()` - always use the AppLogger instance
- Example: `self.logger.call_event("Call initiated")` instead of `logger.info("Call initiated")`

**Important:** The default logging level is CRITICAL (minimal output). Most logging should use `essential()` or higher verbosity methods so it only appears when LOG_LEVEL is explicitly set.

### Changing auto-hangup timer

The 10-second timer is in sip_client.py:287. To modify:
1. Change `threading.Timer(10.0, ...)` to desired duration
2. Update log message at line 290
3. Update return message in `make_call()` at line 268

## Container Build Details

The Containerfile builds PJSIP from source (version 2.16) with specific flags:
- `-fPIC`: Required for Python bindings
- `--disable-video`: Not needed for signaling-only
- `--disable-sound`: NULL audio device (no hardware needed)
- `--enable-shared`: Build shared libraries

Build artifacts are removed after installation to reduce image size (~500-800 MB final).

## Security Considerations

- Application runs as non-root user `dooropener` (UID 1000) in container
- Never commit `.env` file (in .gitignore)
- Health check endpoint at http://localhost:5000 (unauthenticated)
- No authentication on web interface (add if deploying publicly)
- SIP credentials passed via environment variables or mounted .env file
