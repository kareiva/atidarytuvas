import os
import logging
import signal
import sys
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
from sip_client import SIPClient

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Configuration from .env
SIP_PROXY = os.getenv('SIP_PROXY')
SIP_USERNAME = os.getenv('SIP_USERNAME')
SIP_PASSWORD = os.getenv('SIP_PASSWORD')
PHONE_NUMBER = os.getenv('PHONE_NUMBER')
FLASK_PORT = int(os.getenv('FLASK_PORT', 5000))
FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'

# Validate configuration
if not all([SIP_PROXY, SIP_USERNAME, SIP_PASSWORD, PHONE_NUMBER]):
    logger.error("Missing required configuration. Please check your .env file.")
    logger.error("Required: SIP_PROXY, SIP_USERNAME, SIP_PASSWORD, PHONE_NUMBER")

# Initialize SIP client
sip_client = None


def init_sip_client():
    """Initialize and start the SIP client."""
    global sip_client
    try:
        sip_client = SIPClient(SIP_PROXY, SIP_USERNAME, SIP_PASSWORD)
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
    Initiate a call to the configured phone number.

    Returns:
        JSON response with success status and message
    """
    global sip_client

    try:
        # Check if SIP client is initialized
        if not sip_client:
            logger.info("SIP client not initialized, attempting to initialize...")
            if not init_sip_client():
                return jsonify({
                    'success': False,
                    'message': 'Failed to initialize SIP client. Check configuration and logs.'
                }), 500

        # Make the call
        result = sip_client.make_call(PHONE_NUMBER)

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
    logger.info("=" * 60)
    logger.info("Received shutdown signal (Ctrl+C)")
    logger.info("=" * 60)
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
        logger.info("=" * 60)
        logger.info("STARTING GSM DOOR OPENER")
        logger.info("=" * 60)
        logger.info(f"Flask port:   {FLASK_PORT}")
        logger.info(f"Door opener:  {PHONE_NUMBER}")
        logger.info(f"SIP proxy:    {SIP_PROXY}")
        logger.info("=" * 60)
        logger.info("Press Ctrl+C to stop")
        logger.info("")

        # Run Flask in single-threaded mode to avoid PJSIP threading issues
        # PJSIP requires thread registration - single-threaded is simpler for this use case
        app.run(host='0.0.0.0', port=FLASK_PORT, debug=FLASK_DEBUG, threaded=False, use_reloader=False)

    except Exception as e:
        logger.error(f"Application error: {e}")
        shutdown_handler()
        sys.exit(1)
