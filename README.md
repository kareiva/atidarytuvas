# GSM Door Opener - SIP Client with Web Interface

A lightweight Python-based SIP client with a Flask web interface for automated door opening via GSM call. Makes a call to a configured number (GSM door opener) which triggers the door to unlock. The call automatically hangs up after 10 seconds.

## Features

- Simple web interface for door opening control
- **PJSUA2 (PJSIP) library** - Industry-standard, production-ready SIP stack
- **Event-driven callbacks** - Real-time SIP response handling from proxy
- **Clean SIP protocol logging** - Clear request/response pairs with visual indicators
- **Real SIP response code tracking** - See exact codes: 100 Trying, 180 Ringing, 200 OK
- Real-time SIP registration status display in web interface
- Automatic hang-up 10 seconds after call is answered (typical for GSM door openers)
- Only hangs up active/answered calls, not unanswered attempts
- **One-signal shutdown** - Single Ctrl+C cleanly stops application
- **Thread-safe call handling** - Proper locking and signal handling
- Visual feedback for call status in web interface
- Easy configuration via .env file

## Project Structure

```
/home/simonas/sip/
├── app.py              # Main Flask application
├── sip_client.py       # SIP client implementation with enhanced logging
├── .env                # Configuration file (create from .env.example)
├── .env.example        # Example configuration template
├── requirements.txt    # Python dependencies
├── README.md           # This file
├── .gitignore          # Git ignore rules
├── templates/
│   └── index.html      # Web interface
└── static/
    └── style.css       # Styling
```

## Setup Instructions

### 1. Install PJSUA2 (System Package)

**PJSUA2 must be installed via system packages:**

```bash
# Fedora/RHEL
sudo dnf install python3-pjsua2

# Debian/Ubuntu
sudo apt-get install python3-pjsua2
```

**Verify installation:**
```bash
python3 -c "import pjsua2; print('✓ pjsua2 installed')"
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

This will install:
- Flask (web framework)
- python-dotenv (environment configuration)

### 2. Configure SIP Settings

Copy the example configuration file and edit it with your SIP credentials:

```bash
cp .env.example .env
```

Edit `.env` and add your SIP credentials:

```env
SIP_PROXY=sip.example.com
SIP_USERNAME=your_username
SIP_PASSWORD=your_password
PHONE_NUMBER=+1234567890
FLASK_PORT=5000
FLASK_DEBUG=False
```

### 3. Run the Application

```bash
python app.py
```

The application will start on `http://localhost:5000` (or the port specified in .env).

## Usage

1. Open your web browser and navigate to `http://localhost:5000`
2. Check the SIP registration status at the top:
   - **Green (Registered & Ready)**: System is ready to open the door
   - **Yellow (Connecting...)**: System is connecting to SIP server
   - **Red (Not Connected)**: System is not connected, check configuration
3. Click the "Open Door" button to trigger the door opener
4. When the call is answered by the GSM door opener, a 10-second timer starts
5. The call will automatically hang up 10 seconds after being answered
6. If the call is not answered, no auto-hangup occurs
7. Status messages will appear below the button showing the operation result

## Configuration Options

- `SIP_PROXY`: Your SIP server address (e.g., sip.example.com or just example.com)
- `SIP_USERNAME`: Your SIP account username
- `SIP_PASSWORD`: Your SIP account password
- `PHONE_NUMBER`: The GSM door opener's phone number. Can be:
  - Plain number: `+1234567890` (will be formatted as `sip:+1234567890@proxy`)
  - SIP URI: `sip:+1234567890@domain.com` (used as-is)
- `FLASK_PORT`: Port for the web interface (default: 5000)
- `FLASK_DEBUG`: Enable Flask debug mode (default: False)

**Note on Phone Number Format:**
The application automatically formats plain phone numbers as SIP URIs using the format `sip:number@proxy-domain`. If your SIP provider requires a different format, you can specify the full SIP URI directly in the `PHONE_NUMBER` setting.

## Troubleshooting

### Clean SIP Protocol Logging with PJSUA2

The application uses **PJSUA2** with **clean, protocol-focused logging** that shows exactly what's happening:

**Key Features:**
- **Event-driven callbacks** - PJSUA2 provides real callbacks for all SIP events
- **Request/Response pairs** - Every SIP request (INVITE, BYE) is paired with its response (100, 180, 200)
- **Clean visual format** - Clear arrows (──►/◄──) show message direction
- **Exact response codes** - See actual SIP codes from proxy (100, 180, 183, 200, 486, etc.)
- **Minimal noise** - PJSIP internal logs suppressed, focus on call flow
- **One SIGINT shutdown** - Single Ctrl+C cleanly stops the application
- **Production-ready** - PJSUA2 is used in commercial VoIP systems worldwide

The logs clearly show the SIP handshake: `INVITE → 100 Trying → 180 Ringing → 200 OK → BYE`

### SIP Client Fails to Initialize

- Verify your SIP credentials in the .env file
- Check that your SIP proxy address is correct and reachable (don't include "sip:" prefix, just the domain)
- Ensure your firewall allows SIP traffic (usually port 5060 UDP)
- Make sure your network allows SIP/RTP traffic
- Check the `[REGISTRATION]` logs for detailed error messages

### Call Doesn't Connect

**Check the `[SIP-RESPONSE]` logs for exact response codes:**

**Normal call flow with SIP responses:**
1. **100 Trying** - SIP proxy received INVITE
2. **180 Ringing** - Door opener's phone is ringing
3. **200 OK** - Call connected successfully
4. Call remains active for 10 seconds
5. **Call ends** after hangup

**Diagnose issues by SIP response codes:**

**Stuck at 100 Trying (no 180):**
- Number may be unreachable or invalid
- SIP server routing issue
- Check phone number format (E.164: +1234567890)

**Gets 180 Ringing but no 200 OK:**
- Door opener isn't picking up
- Line may be configured to not answer
- Check if device requires specific DTMF tones

**Gets 486 Busy:**
- Line is busy, try again later
- Another call may be in progress

**Gets 603 Decline:**
- Call was rejected by the device
- May indicate configuration issue on door opener

**Gets 487 Request Terminated:**
- Call was cancelled before answer
- May indicate network issue

**No response codes at all:**
- SIP server not responding
- Check network connectivity
- Verify SIP proxy address and credentials
- Ensure UDP port 5060 is not blocked

**Common issues:**
- Verify phone number format (E.164 recommended: +1234567890)
- Check SIP account has outbound calling permissions
- Review `[SIP-RESPONSE]` logs to see exact codes
- Check `[MONITOR]` logs to see state transitions
- NULL audio mode means no audio hardware is needed

### Installation Issues

If you get errors installing dependencies:
- Ensure Python 3.7+ is installed
- For Python 3.13+, `audioop-lts` is required (included in requirements.txt)
- Update pip: `pip install --upgrade pip`
- Check for build tools if compilation fails

### Registration Issues

If the SIP client doesn't register:
- Check `[REGISTRATION]` logs for error messages
- Verify SIP proxy address is correct (without `sip:` prefix in .env)
- Ensure username and password are correct
- Check firewall allows UDP port 5060
- Verify network connectivity to SIP server

### Call Doesn't Progress After "Initiating call"

If the call gets stuck after the "Initiating call" log message:
- The phone number is automatically formatted as a SIP URI (e.g., `sip:+1234567890@domain.com`)
- Check the logs to see the formatted SIP URI under `[CALL]` messages
- If your provider requires a different URI format, specify it directly in `PHONE_NUMBER`
- Ensure RTP ports (typically 10000-20000 UDP) are not blocked
- Look for `[SIP-EVENT]` messages showing call state changes
- If no state changes appear, there may be a network/firewall issue

### Dependencies Installation Issues

If you have issues installing pyVoIP, ensure you have:
- Python 3.7 or higher
- pip updated to the latest version: `pip install --upgrade pip`

**Note for Python 3.13+:** The `audioop` module was removed from the standard library. This project includes `audioop-lts` in requirements.txt, which is a maintained fork that provides the necessary functionality for pyVoIP.

## Logs

The application provides detailed logging of all SIP operations:

### Example Log Output for Successful Call:

```
============================================================
INITIALIZING SIP CLIENT
============================================================
Proxy:    sip.example.com
Username: your_username
Local IP: 192.168.1.100
Mode:     NULL audio (signaling only)
============================================================
──► REGISTER (sending to proxy)
◄── 200 OK (registration successful)
============================================================
✓ SIP CLIENT READY
============================================================

============================================================
INITIATING CALL TO: +37060029810
============================================================
──► INVITE sip:+37060029810@sip.example.com
    Call-ID: abc123xyz
────────────────────────────────────────────────────────────
◄── SIP PROXY RESPONSE: 100
    Trying - Proxy processing request
────────────────────────────────────────────────────────────
────────────────────────────────────────────────────────────
◄── SIP PROXY RESPONSE: 180
    Ringing - Destination is ringing
    ✓ Door opener phone is RINGING
────────────────────────────────────────────────────────────
────────────────────────────────────────────────────────────
◄── SIP PROXY RESPONSE: 200
    OK - Call answered successfully
    ✓ Door opener ANSWERED the call
────────────────────────────────────────────────────────────

⏱  Auto-hangup timer: 10 seconds

────────────────────────────────────────────────────────────
⏱  AUTO-HANGUP: 10 seconds elapsed
────────────────────────────────────────────────────────────
──► BYE (terminating call)
────────────────────────────────────────────────────────────
◄── CALL ENDED
────────────────────────────────────────────────────────────

^C
============================================================
Received shutdown signal (Ctrl+C)
============================================================
✓ SIP client stopped cleanly
Shutdown complete. Exiting...
```

### Log Format:

**SIP Request/Response Flow:**
- `──►` - Outgoing SIP request (INVITE, BYE, REGISTER)
- `◄──` - Incoming SIP response from proxy (100, 180, 200, etc.)
- `⏱` - Timer events
- `✓` - Success indicator
- `✗` - Error indicator
- `════` - Major section separator
- `────` - Response separator

### SIP Response Codes You'll See:
- **100 Trying** - SIP proxy received the request
- **180 Ringing** - Destination phone is ringing
- **183 Session Progress** - Call setup is progressing
- **200 OK** - Call answered successfully
- **486 Busy Here** - Line is busy
- **487 Request Terminated** - Call cancelled
- **603 Decline** - Call rejected

### Call States You'll See:
- **DIALING/TRYING** - Attempting to connect
- **RINGING/EARLY** - Phone is ringing (usually after 180 response)
- **ANSWERED/CONFIRMED** - Call was answered (after 200 OK)
- **ENDED/DISCONNECTED** - Call has ended

## Security Notes

- Never commit your `.env` file to version control
- Keep your SIP credentials secure
- Use HTTPS in production environments
- Consider implementing authentication for the web interface in production

## License

This project is provided as-is for educational and personal use.
