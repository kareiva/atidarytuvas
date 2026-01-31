import threading
import time
import queue
from logger import AppLogger

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

# Logger will be initialized by SIPClient
logger = None


class SIPAccount(pj.Account):
    """Custom Account class to handle registration callbacks."""

    def __init__(self, app_logger: AppLogger, sip_client=None):
        pj.Account.__init__(self)
        self.logger = app_logger
        self.sip_client = sip_client

    def onRegState(self, prm):
        """Called when registration state changes."""
        # Process any pending hangup requests from queue
        if self.sip_client:
            self.sip_client._process_hangup_queue()

        ai = self.getInfo()
        status_code = ai.regStatus
        reason = prm.reason

        self.logger.registration_event(status_code, reason)


class SIPCall(pj.Call):
    """Custom Call class to handle call state callbacks."""

    def __init__(self, account, sip_client=None, app_logger: AppLogger = None):
        pj.Call.__init__(self, account)
        self.sip_client = sip_client
        self.logger = app_logger

    def onCallState(self, prm):
        """Called when call state changes."""
        # Process any pending hangup requests from queue
        # This callback runs in PJSIP thread, so it's safe to call PJSIP functions
        if self.sip_client:
            self.sip_client._process_hangup_queue()

        ci = self.getInfo()
        state = ci.state
        state_text = ci.stateText
        last_status = ci.lastStatusCode
        last_reason = ci.lastReason

        # Map PJSIP states to readable messages
        if state == pj.PJSIP_INV_STATE_CALLING:
            pass  # Initial state, no logging needed

        elif state == pj.PJSIP_INV_STATE_INCOMING:
            self.logger.info("Incoming call")

        elif state == pj.PJSIP_INV_STATE_EARLY:
            if last_status == 100:
                self.logger.sip_response(100, "Trying")
            elif last_status == 180:
                self.logger.sip_response(180, "Ringing")
            elif last_status == 183:
                self.logger.sip_response(183, "Session Progress")

        elif state == pj.PJSIP_INV_STATE_CONNECTING:
            self.logger.info("Call connecting")

        elif state == pj.PJSIP_INV_STATE_CONFIRMED:
            self.logger.sip_response(200, "OK - Call answered")

            # Schedule auto-hangup after 10 seconds
            if self.sip_client:
                self.sip_client._schedule_hangup()

        elif state == pj.PJSIP_INV_STATE_DISCONNECTED:
            if last_status >= 400:
                self.logger.error(f"Call failed: {last_status} {last_reason}")
            else:
                self.logger.call_event("Call ended")

            # Cancel hangup timer if call ends
            if self.sip_client:
                self.sip_client._cancel_hangup_timer()
                self.sip_client.current_call = None

    def onCallMediaState(self, prm):
        """Called when media state changes."""
        # Process any pending hangup requests from queue
        if self.sip_client:
            self.sip_client._process_hangup_queue()

        ci = self.getInfo()
        # For door opener, we don't need actual audio, just signaling
        pass


class SIPClient:
    """SIP client using PJSUA2 library."""

    def __init__(self, proxy, username, password, app_logger: AppLogger = None):
        """
        Initialize the SIP client.

        Args:
            proxy: SIP proxy server address (e.g., 'sips.peoplefone.lt:5060')
            username: SIP username
            password: SIP password
            app_logger: AppLogger instance for logging
        """
        self.proxy = proxy
        self.username = username
        self.password = password
        self.logger = app_logger
        self.endpoint = None
        self.account = None
        self.current_call = None
        self.hangup_timer = None
        self.call_lock = threading.Lock()
        self.hangup_queue = queue.Queue()

    def _process_hangup_queue(self):
        """Process pending hangup requests from queue (called from PJSIP thread)."""
        try:
            # Process all pending hangup requests
            while not self.hangup_queue.empty():
                try:
                    hangup_request = self.hangup_queue.get_nowait()

                    if hangup_request == "HANGUP":
                        self.logger.call_event("Auto-hangup: 10 seconds elapsed")

                        with self.call_lock:
                            if self.current_call:
                                self.logger.sip_request("BYE (terminating call)")
                                try:
                                    # Hangup the call - safe because we're in PJSIP callback
                                    prm = pj.CallOpParam()
                                    self.current_call.hangup(prm)
                                except Exception as e:
                                    self.logger.error(f"Error in hangup: {e}")
                                self.current_call = None

                except queue.Empty:
                    break
        except Exception as e:
            self.logger.error(f"Error processing hangup queue: {e}")

    def start(self):
        """Start the SIP client and register with the server."""
        try:
            self.logger.info("")
            self.logger.info("Initializing SIP client")
            self.logger.info(f"Proxy: {self.proxy}")
            self.logger.info(f"Username: {self.username}")

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

            self.endpoint.audDevManager().setNullDev()
            
            self.logger.sip_request("REGISTER (sending to proxy)")

            # Create account configuration
            acc_cfg = pj.AccountConfig()
            acc_cfg.idUri = f"sip:{self.username}@{self.proxy}"
            acc_cfg.regConfig.registrarUri = f"sip:{self.proxy}"

            # Add credentials
            cred = pj.AuthCredInfo("digest", "*", self.username, 0, self.password)
            acc_cfg.sipConfig.authCreds.append(cred)

            # Create the account
            self.account = SIPAccount(self.logger, self)
            self.account.create(acc_cfg)

            # Wait briefly for registration
            time.sleep(2)

            # Check registration status
            ai = self.account.getInfo()
            if ai.regStatus == 200:
                self.logger.info("SIP client ready")
                return True
            else:
                self.logger.error(f"Registration status: {ai.regStatus} - {ai.regStatusText}")
                return True  # Continue anyway, might still work

        except Exception as e:
            self.logger.error(f"SIP client initialization failed: {e}")
            return False

    def is_registered(self):
        """Check if the SIP client is registered with the server."""
        try:
            if not self.account:
                return False
            ai = self.account.getInfo()
            return ai.regStatus == 200
        except Exception as e:
            self.logger.error(f"Error checking registration status: {e}")
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
                self.logger.error("✗ Cannot make call - SIP client not started")
                return {
                    'success': False,
                    'message': 'SIP client not started'
                }

            with self.call_lock:
                if self.current_call is not None:
                    self.logger.error("✗ Call already in progress")
                    return {
                        'success': False,
                        'message': 'A call is already in progress'
                    }

            self.logger.info(f"Initiating call to: {phone_number}")

            # Format SIP URI (include port as per working example)
            sip_uri = f"sip:{phone_number}@{self.proxy}"
            self.logger.sip_request(f"INVITE {sip_uri}")

            # Create call
            call = SIPCall(self.account, sip_client=self, app_logger=self.logger)

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
            self.logger.error(f"✗ Failed to make call: {e}")
            import traceback
            self.logger.info(traceback.format_exc())
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

                # Use timer to post to queue instead of calling PJSIP directly
                self.hangup_timer = threading.Timer(10.0, self._post_hangup_request)
                self.hangup_timer.start()
                self.logger.info("Auto-hangup timer started (10 seconds)")
        except Exception as e:
            self.logger.error(f"Error scheduling hangup: {e}")

    def _cancel_hangup_timer(self):
        """Cancel the hangup timer."""
        try:
            with self.call_lock:
                if self.hangup_timer:
                    self.hangup_timer.cancel()
                    self.hangup_timer = None
        except Exception as e:
            self.logger.error(f"✗ Error cancelling timer: {e}")

    def _post_hangup_request(self):
        """Post hangup request to queue (called from timer thread)."""
        try:
            # This runs in timer thread - DON'T call PJSIP functions here
            # Just post to queue for worker thread to process
            self.hangup_queue.put("HANGUP")
        except Exception as e:
            self.logger.error(f"✗ Error posting hangup request: {e}")

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
                        self.logger.sip_request("BYE (hanging up active call)")
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

            self.logger.info("SIP client stopped")

        except Exception as e:
            self.logger.error(f"Error stopping SIP client: {e}")
