# GSM Door Opener - SIP Client with Web Interface

A lightweight Python-based SIP client with a Flask web interface for automated door opening via GSM call. Supports multiple targets (DOOR, GATE, GARAGE, etc.) with individual phone numbers. Makes a call to the selected target which triggers the device to unlock. The call automatically hangs up after 10 seconds.

## Features

- Simple web interface for door opening control
- **Multiple targets support** - Configure multiple phone numbers (DOOR, GATE, GARAGE, etc.)
- **PJSUA2 (PJSIP) library** - Industry-standard, production-ready SIP stack
- Automatic hang-up 10 seconds after call is answered (typical for GSM door openers)
- **Thread-safe call handling** - Proper locking and signal handling
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
PHONE_NUMBER_TEST=+1234567890
FLASK_PORT=5000
FLASK_DEBUG=False
```

### 3. Run the Application

```bash
python app.py
```

The application will start on `http://localhost:5000` (or the port specified in .env).


- `SIP_PROXY`: Your SIP server address (e.g., sip.example.com or just example.com)
- `SIP_USERNAME`: Your SIP account username
- `SIP_PASSWORD`: Your SIP account password
- `PHONE_NUMBER_<NAME>`: Multiple phone numbers for different targets. Can be:
  - Plain number: `+1234567890` (will be formatted as `sip:+1234567890@proxy`)
  - SIP URI: `sip:+1234567890@domain.com` (used as-is)
  - Examples:
    - `PHONE_NUMBER_DOOR=+1234567890`
    - `PHONE_NUMBER_GATE=+0987654321`
    - `PHONE_NUMBER_GARAGE=+1122334455`
- `FLASK_PORT`: Port for the web interface (default: 5000)
- `FLASK_DEBUG`: Enable Flask debug mode (default: False)
- `LOG_LEVEL`: Logging verbosity (default: ERROR if not set)
  - Not set or `ERROR`: Errors only (minimal output)
  - `INFO`: Errors + informational messages (SIP events, registration, calls)

**Note on Phone Number Format:**
The application automatically formats plain phone numbers as SIP URIs using the format `sip:number@proxy-domain`. If your SIP provider requires a different format, you can specify the full SIP URI directly in the phone number setting.

**Adding New Targets:**
Simply add a new `PHONE_NUMBER_<NAME>=<number>` line to your .env file and restart the application. The web interface will automatically create a button for the new target.

## Security Notes

- Never commit your `.env` file to version control
- Consider implementing authentication for the web interface in production

## License

This project is provided as-is for educational and personal use.
