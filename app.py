import os
import logging
import signal
import sys
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
from sip_client import SIPClient
from logger import create_logger

# Load environment variables
load_dotenv()

# Disable Flask's default logging to prevent duplicates
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# Create application logger with configured verbosity
# Empty string = CRITICAL (minimal output), or set LOG_LEVEL=LOW/MEDIUM/HIGH
LOG_LEVEL = os.getenv('LOG_LEVEL', '').upper()
logger = create_logger('app', LOG_LEVEL)

# Initialize Flask app
app = Flask(__name__)

# Configuration from .env
SIP_PROXY = os.getenv('SIP_PROXY')
SIP_USERNAME = os.getenv('SIP_USERNAME')
SIP_PASSWORD = os.getenv('SIP_PASSWORD')
FLASK_PORT = int(os.getenv('FLASK_PORT', 5000))
FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'

# Parse multiple phone numbers from environment variables
# Format: PHONE_NUMBER_<NAME>=<number>
# Example: PHONE_NUMBER_DOOR=+1234567890, PHONE_NUMBER_GATE=+0987654321
PHONE_NUMBERS = {}
for key, value in os.environ.items():
    if key.startswith('PHONE_NUMBER_'):
        name = key.replace('PHONE_NUMBER_', '')
        PHONE_NUMBERS[name] = value
        logger.info(f"Loaded phone number: {name} -> {value}")

# Validate configuration
if not all([SIP_PROXY, SIP_USERNAME, SIP_PASSWORD]):
    logger.error("Missing required configuration. Please check your .env file.")
    logger.error("Required: SIP_PROXY, SIP_USERNAME, SIP_PASSWORD")

if not PHONE_NUMBERS:
    logger.error("No phone numbers configured. Please add PHONE_NUMBER_<NAME> variables to .env")
    logger.error("Example: PHONE_NUMBER_DOOR=+1234567890")

# Initialize SIP client
sip_client = None


def init_sip_client():
    """Initialize and start the SIP client."""
    global sip_client
    try:
        # Create a separate logger for SIP client
        sip_logger = create_logger('sip_client', LOG_LEVEL)
        sip_client = SIPClient(SIP_PROXY, SIP_USERNAME, SIP_PASSWORD, sip_logger)
        success = sip_client.start()
        if success:
            logger.info("SIP client initialized successfully")
        else:
            logger.error("Failed to initialize SIP client")
        return success
    except Exception as e:
        logger.error(f"Error initializing SIP client: {e}")
        return False


@app.route('/')
def index():
    """Render the home page."""
    return render_template('index.html')


@app.route('/call', methods=['POST'])
def make_call():
    """
    Initiate a call to the specified phone number.

    Request JSON:
        {
            "target": "DOOR"  // Name of the target (e.g., DOOR, GATE)
        }

    Returns:
        JSON response with success status and message
    """
    global sip_client

    try:
        # Get target from request
        data = request.get_json()
        target = data.get('target') if data else None

        if not target:
            return jsonify({
                'success': False,
                'message': 'No target specified'
            }), 400

        # Check if target exists
        if target not in PHONE_NUMBERS:
            return jsonify({
                'success': False,
                'message': f'Unknown target: {target}'
            }), 400

        phone_number = PHONE_NUMBERS[target]

        # Check if SIP client is initialized
        if not sip_client:
            logger.info("SIP client not initialized, attempting to initialize...")
            if not init_sip_client():
                return jsonify({
                    'success': False,
                    'message': 'Failed to initialize SIP client. Check configuration and logs.'
                }), 500

        # Make the call
        result = sip_client.make_call(phone_number)

        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 500

    except Exception as e:
        logger.error(f"Error in /call endpoint: {e}")
        return jsonify({
            'success': False,
            'message': f'Internal error: {str(e)}'
        }), 500


@app.route('/status', methods=['GET'])
def status():
    """
    Check the status of the SIP client.

    Returns:
        JSON response with status information
    """
    if sip_client:
        client_status = sip_client.get_status()
        return jsonify({
            'sip_client_initialized': True,
            'registered': client_status['registered'],
            'has_active_call': client_status['has_active_call'],
            'proxy': SIP_PROXY
        })
    else:
        return jsonify({
            'sip_client_initialized': False,
            'registered': False,
            'has_active_call': False,
            'proxy': SIP_PROXY
        })


@app.route('/targets', methods=['GET'])
def get_targets():
    """
    Get the list of available call targets.

    Returns:
        JSON response with list of targets
    """
    return jsonify({
        'targets': list(PHONE_NUMBERS.keys())
    })


@app.before_request
def before_first_request():
    """Initialize SIP client before first request."""
    global sip_client
    if sip_client is None:
        init_sip_client()


def shutdown_handler():
    """Clean up resources on shutdown."""
    global sip_client
    if sip_client:
        logger.info("Shutting down SIP client...")
        sip_client.stop()
        sip_client = None


def signal_handler(sig, frame):
    """Handle SIGINT (Ctrl+C) signal."""
    logger.info("")
    logger.info("Received shutdown signal (Ctrl+C)")
    shutdown_handler()
    logger.info("Shutdown complete. Exiting...")
    sys.exit(0)


if __name__ == '__main__':
    # Register signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Register atexit as fallback
    import atexit
    atexit.register(shutdown_handler)

    try:
        logger.info("")
        logger.info("Starting GSM Door Opener")
        logger.info(f"Flask port: {FLASK_PORT}")
        logger.info(f"SIP proxy: {SIP_PROXY}")
        logger.info(f"Log level: {LOG_LEVEL if LOG_LEVEL else 'ERROR'}")
        logger.info(f"Configured targets: {len(PHONE_NUMBERS)}")
        for name, number in PHONE_NUMBERS.items():
            logger.info(f"  {name}: {number}")
        logger.info("Press Ctrl+C to stop")
        logger.info("")

        # Run Flask in single-threaded mode to avoid PJSIP threading issues
        # PJSIP requires thread registration - single-threaded is simpler for this use case
        app.run(host='0.0.0.0', port=FLASK_PORT, debug=FLASK_DEBUG, threaded=False, use_reloader=False)

    except Exception as e:
        logger.error(f"Application error: {e}")
        shutdown_handler()
        sys.exit(1)
