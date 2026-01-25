import threading
import time
import logging

try:
    import pjsua2 as pj
except ImportError:
    print("=" * 60)
    print("ERROR: pjsua2 not found!")
    print("=" * 60)
    print("Install via system packages:")
    print("  Fedora/RHEL: sudo dnf install python3-pjsua2")
    print("  Debian/Ubuntu: sudo apt-get install python3-pjsua2")
    print("=" * 60)
    raise

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SIPAccount(pj.Account):
    """Custom Account class to handle registration callbacks."""

    def __init__(self):
        pj.Account.__init__(self)

    def onRegState(self, prm):
        """Called when registration state changes."""
        ai = self.getInfo()
        status_code = ai.regStatus
        reason = prm.reason

        if status_code == 200:
            logger.info(f"◄── {status_code} OK (registration successful)")
        else:
            logger.info(f"◄── {status_code} {reason}")


class SIPCall(pj.Call):
    """Custom Call class to handle call state callbacks."""

    def __init__(self, account, sip_client=None):
        pj.Call.__init__(self, account)
        self.sip_client = sip_client

    def onCallState(self, prm):
        """Called when call state changes."""
        ci = self.getInfo()
        state = ci.state
        state_text = ci.stateText
        last_status = ci.lastStatusCode
        last_reason = ci.lastReason

        logger.info("─" * 60)
        logger.info(f"◄── SIP PROXY RESPONSE: {last_status}")

        # Map PJSIP states to readable messages
        if state == pj.PJSIP_INV_STATE_CALLING:
            logger.info("    Calling - Sending INVITE")

        elif state == pj.PJSIP_INV_STATE_INCOMING:
            logger.info("    Incoming - Receiving call")

        elif state == pj.PJSIP_INV_STATE_EARLY:
            if last_status == 100:
                logger.info("    Trying - Request received")
            elif last_status == 180:
                logger.info("    Ringing - Destination is ringing")
                logger.info("    ✓ Door opener phone is RINGING")
            elif last_status == 183:
                logger.info("    Session Progress - Call progressing")

        elif state == pj.PJSIP_INV_STATE_CONNECTING:
            logger.info("    Connecting - Call being connected")

        elif state == pj.PJSIP_INV_STATE_CONFIRMED:
            logger.info("    OK - Call answered successfully")
            logger.info("    ✓ Door opener ANSWERED the call")
            logger.info("─" * 60)

            # Schedule auto-hangup after 10 seconds
            if self.sip_client:
                self.sip_client._schedule_hangup()

        elif state == pj.PJSIP_INV_STATE_DISCONNECTED:
            if last_status >= 400:
                logger.info(f"    Error - {last_reason}")
                if last_status == 486:
                    logger.info("    Busy Here - Line is busy")
                elif last_status == 603:
                    logger.info("    Decline - Call rejected")
            else:
                logger.info("    Call ended")
            logger.info("─" * 60)

            # Cancel hangup timer if call ends
            if self.sip_client:
                self.sip_client._cancel_hangup_timer()
                self.sip_client.current_call = None

        else:
            logger.info(f"    State: {state_text}")
            logger.info("─" * 60)

    def onCallMediaState(self, prm):
        """Called when media state changes."""
        ci = self.getInfo()
        # For door opener, we don't need actual audio, just signaling
        pass


class SIPClient:
    """SIP client using PJSUA2 library."""

    def __init__(self, proxy, username, password):
        """
        Initialize the SIP client.

        Args:
            proxy: SIP proxy server address (e.g., 'sips.peoplefone.lt:5060')
            username: SIP username
            password: SIP password
        """
        self.proxy = proxy
        self.username = username
        self.password = password
        self.endpoint = None
        self.account = None
        self.current_call = None
        self.hangup_timer = None
        self.call_lock = threading.Lock()

    def start(self):
        """Start the SIP client and register with the server."""
        try:
            logger.info("=" * 60)
            logger.info("INITIALIZING SIP CLIENT (PJSUA2)")
            logger.info("=" * 60)
            logger.info(f"Proxy:    {self.proxy}")
            logger.info(f"Username: {self.username}")
            logger.info("=" * 60)

            # Create and initialize endpoint
            ep_cfg = pj.EpConfig()
            self.endpoint = pj.Endpoint()
            self.endpoint.libCreate()

            # Configure logging (suppress PJSIP verbose logs)
            log_cfg = pj.LogConfig()
            log_cfg.level = 3  # Show only warnings and errors from PJSIP
            log_cfg.consoleLevel = 0  # Don't output to console
            ep_cfg.logConfig = log_cfg

            self.endpoint.libInit(ep_cfg)

            # Create SIP transport
            sip_tp_config = pj.TransportConfig()
            sip_tp_config.port = 5060
            self.endpoint.transportCreate(pj.PJSIP_TRANSPORT_UDP, sip_tp_config)

            # Start the library
            self.endpoint.libStart()

            logger.info("──► REGISTER (sending to proxy)")

            # Create account configuration
            acc_cfg = pj.AccountConfig()
            acc_cfg.idUri = f"sip:{self.username}@{self.proxy}"
            acc_cfg.regConfig.registrarUri = f"sip:{self.proxy}"

            # Add credentials
            cred = pj.AuthCredInfo("digest", "*", self.username, 0, self.password)
            acc_cfg.sipConfig.authCreds.append(cred)

            # Create the account
            self.account = SIPAccount()
            self.account.create(acc_cfg)

            # Wait briefly for registration
            time.sleep(2)

            # Check registration status
            ai = self.account.getInfo()
            if ai.regStatus == 200:
                logger.info("=" * 60)
                logger.info("✓ SIP CLIENT READY")
                logger.info("=" * 60)
                return True
            else:
                logger.warning(f"Registration status: {ai.regStatus} - {ai.regStatusText}")
                return True  # Continue anyway, might still work

        except Exception as e:
            logger.error(f"✗ SIP client initialization failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def is_registered(self):
        """Check if the SIP client is registered with the server."""
        try:
            if not self.account:
                return False
            ai = self.account.getInfo()
            return ai.regStatus == 200
        except Exception as e:
            logger.error(f"✗ Error checking registration status: {e}")
            return False

    def get_status(self):
        """Get the current status of the SIP client."""
        return {
            'initialized': self.endpoint is not None and self.account is not None,
            'registered': self.is_registered(),
            'has_active_call': self.current_call is not None,
            'proxy': self.proxy,
            'username': self.username
        }

    def make_call(self, phone_number):
        """
        Make a call to the specified phone number.

        Args:
            phone_number: Phone number to call

        Returns:
            dict: Status of the call attempt
        """
        try:
            if not self.endpoint or not self.account:
                logger.error("✗ Cannot make call - SIP client not started")
                return {
                    'success': False,
                    'message': 'SIP client not started'
                }

            with self.call_lock:
                if self.current_call is not None:
                    logger.warning("✗ Call already in progress")
                    return {
                        'success': False,
                        'message': 'A call is already in progress'
                    }

            logger.info("")
            logger.info("=" * 60)
            logger.info(f"INITIATING CALL TO: {phone_number}")
            logger.info("=" * 60)

            # Format SIP URI (include port as per working example)
            sip_uri = f"sip:{phone_number}@{self.proxy}"
            logger.info(f"──► INVITE {sip_uri}")

            # Create call
            call = SIPCall(self.account, sip_client=self)

            # Make call with CallOpParam
            prm = pj.CallOpParam(True)  # True = use default settings
            call.makeCall(sip_uri, prm)

            with self.call_lock:
                self.current_call = call

            return {
                'success': True,
                'message': 'Call initiated. Will hang up 10 seconds after answer.'
            }

        except Exception as e:
            logger.error(f"✗ Failed to make call: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                'success': False,
                'message': f'Failed to make call: {str(e)}'
            }

    def _schedule_hangup(self):
        """Schedule automatic hangup 10 seconds after call is answered."""
        try:
            with self.call_lock:
                if self.hangup_timer:
                    self.hangup_timer.cancel()

                self.hangup_timer = threading.Timer(10.0, self._auto_hangup)
                self.hangup_timer.start()
                logger.info("")
                logger.info("⏱  Auto-hangup timer: 10 seconds")
                logger.info("")
        except Exception as e:
            logger.error(f"✗ Error scheduling hangup: {e}")

    def _cancel_hangup_timer(self):
        """Cancel the hangup timer."""
        try:
            with self.call_lock:
                if self.hangup_timer:
                    self.hangup_timer.cancel()
                    self.hangup_timer = None
        except Exception as e:
            logger.error(f"✗ Error cancelling timer: {e}")

    def _auto_hangup(self):
        """Automatically hang up the current call."""
        try:
            logger.info("")
            logger.info("─" * 60)
            logger.info("⏱  AUTO-HANGUP: 10 seconds elapsed")
            logger.info("─" * 60)

            with self.call_lock:
                if self.current_call:
                    logger.info("──► BYE (terminating call)")
                    # Hangup the call
                    prm = pj.CallOpParam()
                    self.current_call.hangup(prm)
                    self.current_call = None

        except Exception as e:
            logger.error(f"✗ Error during auto hang-up: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def stop(self):
        """Stop the SIP client."""
        try:
            with self.call_lock:
                # Cancel timer
                if self.hangup_timer:
                    self.hangup_timer.cancel()
                    self.hangup_timer = None

                # Hangup active call
                if self.current_call:
                    try:
                        logger.info("──► BYE (hanging up active call)")
                        prm = pj.CallOpParam()
                        self.current_call.hangup(prm)
                    except:
                        pass
                    self.current_call = None

            # Destroy account
            if self.account:
                try:
                    del self.account
                    self.account = None
                except:
                    pass

            # Destroy endpoint
            if self.endpoint:
                try:
                    self.endpoint.libDestroy()
                    del self.endpoint
                    self.endpoint = None
                except:
                    pass

            logger.info("✓ SIP client stopped cleanly")

        except Exception as e:
            logger.error(f"✗ Error stopping SIP client: {e}")
