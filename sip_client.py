import os
import socket
import threading
import time
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


class CallMetrics:
    """Thread-safe in-process metrics for the Prometheus /metrics endpoint.

    Everything is labelled by `target` (DOOR/GATE/LAB) so a broken target is
    distinguishable from a working one. `result` is the call outcome:
      ringing   - got a provisional (100/180/183): the far end was reached
      answered  - call was answered (200/CONFIRMED)
      cancelled - we hung up after the ring window (normal for ring-and-open)
      rejected  - provider/callee ended it with a 4xx/5xx/6xx we did NOT cause
      error     - local error making the call
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.attempts = {}          # target -> count
        self.results = {}           # (target, result) -> count
        self.last_status = {}       # target -> last SIP status code (final)
        self.last_reason = {}       # target -> last SIP reason text
        self.last_ts = {}           # target -> epoch of last attempt
        self.last_ring_seconds = {}     # target -> seconds INVITE->first provisional
        self.last_duration_seconds = {}  # target -> seconds INVITE->disconnect
        self.last_reached = {}      # target -> 1 if a provisional/answer was seen, else 0
        # Provisional responses (100/180/183...) prove the trunk actually answered
        # the INVITE vs dropping it silently.
        self.provisional = {}       # (target, code) -> count
        self.first_response = {}    # target -> first provisional code of last call (0=none)
        self.in_progress = 0        # 1 while a call is active
        # Registration health.
        self.reg_status = 0         # last registration status code
        self.reg_last_ok_ts = 0.0   # epoch of last successful (200) registration
        self.reg_success_total = 0
        self.reg_failure_total = 0

    def record_attempt(self, target):
        with self.lock:
            self.attempts[target] = self.attempts.get(target, 0) + 1
            self.last_ts[target] = time.time()
            self.last_reached[target] = 0
            self.first_response[target] = 0

    def record_provisional(self, target, code):
        with self.lock:
            self.provisional[(target, code)] = self.provisional.get((target, code), 0) + 1
            if not self.first_response.get(target):
                self.first_response[target] = code

    def record_registration(self, code):
        with self.lock:
            self.reg_status = code
            if code == 200:
                self.reg_success_total += 1
                self.reg_last_ok_ts = time.time()
            elif code >= 300:
                self.reg_failure_total += 1

    def set_in_progress(self, v):
        with self.lock:
            self.in_progress = 1 if v else 0

    def record_result(self, target, result, status=None, reason=None,
                      ring_seconds=None, duration_seconds=None, reached=None):
        with self.lock:
            self.results[(target, result)] = self.results.get((target, result), 0) + 1
            if status is not None:
                self.last_status[target] = status
            if reason is not None:
                self.last_reason[target] = reason
            if ring_seconds is not None:
                self.last_ring_seconds[target] = ring_seconds
            if duration_seconds is not None:
                self.last_duration_seconds[target] = duration_seconds
            if reached is not None:
                self.last_reached[target] = 1 if reached else 0

    def mark_reached(self, target, ring_seconds):
        with self.lock:
            if self.last_reached.get(target) != 1:
                self.last_reached[target] = 1
                self.last_ring_seconds[target] = ring_seconds


def _esc(v):
    """Escape a Prometheus label value."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def classify_result(answered, we_hung_up, reached, last_status):
    """Classify a finished call. Pure function (unit-tested).

    answered    - call reached CONFIRMED (200)
    we_hung_up  - our auto-hangup/BYE/CANCEL initiated the teardown
    reached     - a provisional (100/180/183) or answer was seen
    last_status - final SIP status code (0 if none)

    Returns one of: answered | no_response | cancelled | rejected | ended.
    """
    if answered:
        return "answered"
    if we_hung_up and not reached:
        # We cancelled and the trunk never sent a provisional: the INVITE was
        # dropped/unanswered (call not delivered).
        return "no_response"
    if we_hung_up and last_status in (0, 200, 487):
        # Our CANCEL/BYE after the far end rang: normal ring-and-open path.
        return "cancelled"
    if last_status >= 400:
        # Provider/callee ended it and it was not our cancel.
        return "rejected"
    return "ended"


class SIPAccount(pj.Account):
    """Custom Account class to handle registration callbacks."""

    def __init__(self, app_logger: AppLogger, sip_client=None):
        pj.Account.__init__(self)
        self.logger = app_logger
        self.sip_client = sip_client

    def onRegState(self, prm):
        """Called when registration state changes."""
        ai = self.getInfo()
        status_code = ai.regStatus
        reason = prm.reason
        self.logger.registration_event(status_code, reason)
        if self.sip_client and self.sip_client.metrics:
            self.sip_client.metrics.record_registration(status_code)


# Readable names for the PJSIP INVITE session states.
def _state_name(pj_mod, state):
    for n in ("PJSIP_INV_STATE_NULL", "PJSIP_INV_STATE_CALLING",
              "PJSIP_INV_STATE_INCOMING", "PJSIP_INV_STATE_EARLY",
              "PJSIP_INV_STATE_CONNECTING", "PJSIP_INV_STATE_CONFIRMED",
              "PJSIP_INV_STATE_DISCONNECTED"):
        if getattr(pj_mod, n, None) == state:
            return n.replace("PJSIP_INV_STATE_", "")
    return str(state)


class SIPCall(pj.Call):
    """Custom Call class to handle call state callbacks."""

    def __init__(self, account, sip_client=None, app_logger: AppLogger = None,
                 target="?", metrics=None, t0=None):
        pj.Call.__init__(self, account)
        self.sip_client = sip_client
        self.logger = app_logger
        self.target = target
        self.metrics = metrics
        self.t0 = t0 or time.time()
        self.reached = False      # saw a provisional or answer
        self.answered = False
        self.final_recorded = False

    def _elapsed(self):
        return time.time() - self.t0

    def onCallState(self, prm):
        """Called when call state changes. Logs EVERY transition with the
        actual SIP status code, reason and elapsed time since INVITE."""
        ci = self.getInfo()
        state = ci.state
        state_text = ci.stateText
        last_status = ci.lastStatusCode
        last_reason = ci.lastReason
        elapsed = self._elapsed()

        self.logger.call_event(
            "[{0}] state={1} sip={2} {3} (+{4:.1f}s)".format(
                self.target, _state_name(pj, state), last_status,
                last_reason, elapsed))

        if state == pj.PJSIP_INV_STATE_EARLY:
            # First provisional response => the far end was actually reached.
            if not self.reached:
                self.reached = True
                if self.metrics:
                    self.metrics.mark_reached(self.target, elapsed)
            if self.metrics and last_status:
                self.metrics.record_provisional(self.target, last_status)
            names = {100: "Trying", 180: "Ringing", 181: "Call Is Being Forwarded",
                     182: "Queued", 183: "Session Progress"}
            if last_status in names:
                self.logger.sip_response(last_status, names[last_status])

        elif state == pj.PJSIP_INV_STATE_CONFIRMED:
            self.answered = True
            self.reached = True
            self.logger.sip_response(200, "OK - Call answered")

        elif state == pj.PJSIP_INV_STATE_DISCONNECTED:
            self._record_final(last_status, last_reason, elapsed)
            if self.sip_client:
                self.sip_client._clear_call()

    def _record_final(self, last_status, last_reason, elapsed):
        if self.final_recorded:
            return
        self.final_recorded = True

        # Did WE end it? (auto-hangup after the ring window, or app shutdown)
        we_hung_up = bool(self.sip_client and getattr(self.sip_client, "_last_hangup_by_us", False))

        result = classify_result(self.answered, we_hung_up, self.reached, last_status)

        if result == "rejected":
            self.logger.error(
                "[{0}] Call FAILED: {1} {2} (reached={3}, +{4:.1f}s) — provider/callee ended it".format(
                    self.target, last_status, last_reason, self.reached, elapsed))
        elif result == "no_response":
            self.logger.error(
                "[{0}] NO RESPONSE: trunk never answered the INVITE (0 provisional, {1:.1f}s, last sip={2}) — call not delivered".format(
                    self.target, elapsed, last_status))
        elif result == "cancelled":
            self.logger.call_event(
                "[{0}] Call cancelled by us after {1:.1f}s (reached={2}, last sip={3}) — normal ring-and-open".format(
                    self.target, elapsed, self.reached, last_status))
        else:
            self.logger.call_event(
                "[{0}] Call {1}: {2} {3} (+{4:.1f}s)".format(
                    self.target, result, last_status, last_reason, elapsed))

        if self.metrics:
            self.metrics.record_result(
                self.target, result, status=last_status, reason=last_reason,
                duration_seconds=elapsed, reached=self.reached)

    def onCallMediaState(self, prm):
        """Called when media state changes."""
        pass


class SIPClient:
    """SIP client using PJSUA2 library."""

    def __init__(self, proxy, username, password, app_logger: AppLogger = None,
                 hangup_timeout: int = 10):
        self.proxy = proxy
        self.username = username
        self.password = password
        self.logger = app_logger
        self.hangup_timeout = hangup_timeout
        self.endpoint = None
        self.account = None
        self.current_call = None
        self.hangup_timer = None
        self.call_lock = threading.Lock()
        self.metrics = CallMetrics()
        self._last_hangup_by_us = False
        # PJSIP log verbosity: 0=silent .. 5=very verbose. Level >=4 prints full
        # SIP messages (INVITE/180/183/486/603/CANCEL/BYE) to stdout -> journal.
        self.pjsip_log_level = int(os.getenv("PJSIP_LOG_LEVEL", "4"))

    def _resolve_proxy(self, proxy):
        host, sep, port = proxy.partition(':')
        try:
            ip = socket.gethostbyname(host)
        except socket.gaierror as e:
            self.logger.error(f"Could not resolve SIP proxy '{host}': {e}. Using unresolved value.")
            return proxy
        return f"{ip}{sep}{port}" if sep else ip

    def start(self):
        """Start the SIP client and register with the server."""
        try:
            self.logger.info("")
            self.logger.info("Initializing SIP client")
            self.logger.info(f"Proxy: {self.proxy}")
            self.logger.info(f"Username: {self.username}")

            # Use the proxy FQDN in SIP URIs (like Linphone) and let pjsip do DNS.
            # Rewriting it to a raw IP (old _resolve_proxy behaviour) makes the
            # From/Request-URI domain an IP, which peoplefone's SBC accepts for
            # REGISTER but silently drops for outbound INVITEs. Opt back in with
            # SIP_RESOLVE_PROXY=true if ever needed.
            if os.getenv("SIP_RESOLVE_PROXY", "false").lower() in ("1", "true", "yes"):
                self.proxy = self._resolve_proxy(self.proxy)
                self.logger.info(f"Resolved proxy address: {self.proxy}")
            else:
                self.logger.info(f"Using proxy FQDN (DNS via pjsip): {self.proxy}")

            # Create and initialize endpoint
            ep_cfg = pj.EpConfig()
            self.endpoint = pj.Endpoint()
            self.endpoint.libCreate()

            # PJSIP logging: level controls what is written; consoleLevel controls
            # what reaches stdout. >=4 shows full SIP message traces (so provider
            # responses like 486/603/408 are visible per call). Configurable via
            # $PJSIP_LOG_LEVEL.
            log_cfg = pj.LogConfig()
            log_cfg.level = self.pjsip_log_level
            log_cfg.consoleLevel = self.pjsip_log_level
            ep_cfg.logConfig = log_cfg
            self.logger.info(f"PJSIP log level: {self.pjsip_log_level}")

            self.endpoint.libInit(ep_cfg)

            # Create SIP transport
            sip_tp_config = pj.TransportConfig()
            sip_tp_config.port = 5060
            self.endpoint.transportCreate(pj.PJSIP_TRANSPORT_UDP, sip_tp_config)

            # Start the library
            self.endpoint.libStart()
            self.endpoint.audDevManager().setNullDev()

            # Shrink the INVITE below the path MTU. The default SDP offers ~11
            # audio codecs + a text media line (~1458-byte INVITE); the route to
            # the proxy is tunnelled (PMTU ~1420), so the oversized INVITE is
            # dropped (DF set) while small REGISTER/OPTIONS pass — calls silently
            # fail. A door opener needs no real audio, so keep only PCMU/PCMA.
            try:
                try:
                    codecs = self.endpoint.codecEnum2()
                except Exception:
                    codecs = self.endpoint.codecEnum()
                # Keep only PCMU (G.711u). One codec keeps the INVITE well under
                # the ~1420 PMTU even after the Proxy-Authorization header is added
                # on the authenticated re-INVITE. Media isn't used (null dev).
                kept = []
                for ci in codecs:
                    cid = ci.codecId
                    keep = cid.startswith("PCMU/")
                    self.endpoint.codecSetPriority(cid, 255 if keep else 0)
                    if keep:
                        kept.append(cid)
                self.logger.info(f"Audio codecs limited to {kept} (shrink INVITE < PMTU)")
            except Exception as e:
                self.logger.error(f"Could not limit codecs: {e}")

            self.logger.sip_request("REGISTER (sending to proxy)")

            # Create account configuration
            acc_cfg = pj.AccountConfig()
            acc_cfg.idUri = f"sip:{self.username}@{self.proxy}"
            acc_cfg.regConfig.registrarUri = f"sip:{self.proxy}"

            cred = pj.AuthCredInfo("digest", "*", self.username, 0, self.password)
            acc_cfg.sipConfig.authCreds.append(cred)

            self.account = SIPAccount(self.logger, self)
            self.account.create(acc_cfg)

            time.sleep(2)

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
        try:
            if not self.account:
                return False
            ai = self.account.getInfo()
            return ai.regStatus == 200
        except Exception as e:
            self.logger.error(f"Error checking registration status: {e}")
            return False

    def get_status(self):
        return {
            'initialized': self.endpoint is not None and self.account is not None,
            'registered': self.is_registered(),
            'has_active_call': self.current_call is not None,
            'proxy': self.proxy,
            'username': self.username
        }

    def make_call(self, phone_number, target="?"):
        """Make a call to the specified phone number (labelled by `target`)."""
        try:
            if not self.endpoint or not self.account:
                self.logger.error("✗ Cannot make call - SIP client not started")
                return {'success': False, 'message': 'SIP client not started'}

            with self.call_lock:
                if self.current_call is not None:
                    self.logger.error("✗ Call already in progress")
                    return {'success': False, 'message': 'A call is already in progress'}

            self.metrics.record_attempt(target)
            self._last_hangup_by_us = False
            t0 = time.time()

            self.logger.info(f"[{target}] Initiating call to: {phone_number}")
            sip_uri = f"sip:{phone_number}@{self.proxy}"
            self.logger.sip_request(f"INVITE {sip_uri}")

            call = SIPCall(self.account, sip_client=self, app_logger=self.logger,
                           target=target, metrics=self.metrics, t0=t0)

            prm = pj.CallOpParam(True)
            call.makeCall(sip_uri, prm)

            with self.call_lock:
                self.current_call = call
                self.hangup_timer = threading.Timer(self.hangup_timeout, self._post_hangup_request)
                self.hangup_timer.start()
                self.metrics.set_in_progress(True)
                self.logger.info(f"[{target}] Auto-hangup timer started ({self.hangup_timeout}s)")

            return {'success': True,
                    'message': f'Call initiated. Will hang up in {self.hangup_timeout} seconds.'}

        except Exception as e:
            self.logger.error(f"✗ Failed to make call: {e}")
            import traceback
            self.logger.info(traceback.format_exc())
            self.metrics.record_result(target, "error", reason=str(e))
            return {'success': False, 'message': f'Failed to make call: {str(e)}'}

    def _clear_call(self):
        with self.call_lock:
            if self.hangup_timer:
                self.hangup_timer.cancel()
                self.hangup_timer = None
            self.current_call = None
        self.metrics.set_in_progress(False)

    def _post_hangup_request(self):
        """Timer thread: register with PJSIP and hang up the ringing call."""
        try:
            ep = pj.Endpoint.instance()
            try:
                ep.libRegisterThread("hangup_timer")
            except Exception:
                pass
        except Exception as e:
            self.logger.error(f"Error in auto-hangup setup: {e}")

        self.logger.call_event(f"Auto-hangup: {self.hangup_timeout}s elapsed")
        with self.call_lock:
            call = self.current_call
            self.current_call = None
            self.hangup_timer = None

        if call:
            try:
                ci = call.getInfo()
                if ci.state != pj.PJSIP_INV_STATE_DISCONNECTED:
                    self._last_hangup_by_us = True
                    self.logger.sip_request("BYE/CANCEL (auto-hangup)")
                    prm = pj.CallOpParam()
                    call.hangup(prm)
            except Exception as e:
                self.logger.error(f"Error hanging up call: {e}")

    def stop(self):
        try:
            with self.call_lock:
                if self.hangup_timer:
                    self.hangup_timer.cancel()
                    self.hangup_timer = None
                if self.current_call:
                    try:
                        self._last_hangup_by_us = True
                        self.logger.sip_request("BYE (hanging up active call)")
                        prm = pj.CallOpParam()
                        self.current_call.hangup(prm)
                    except Exception:
                        pass
                    self.current_call = None

            if self.account:
                try:
                    del self.account
                    self.account = None
                except Exception:
                    pass

            if self.endpoint:
                try:
                    self.endpoint.libDestroy()
                    del self.endpoint
                    self.endpoint = None
                except Exception:
                    pass

            self.logger.info("SIP client stopped")
        except Exception as e:
            self.logger.error(f"Error stopping SIP client: {e}")

    def render_metrics(self):
        """Prometheus text exposition of call metrics + registration state."""
        m = self.metrics
        lines = []
        reg = 1 if self.is_registered() else 0
        lines.append("# HELP gate_build_info Build/runtime info (constant 1).")
        lines.append("# TYPE gate_build_info gauge")
        lines.append('gate_build_info{version="%s",pjsip="%s",proxy="%s",username="%s"} 1' % (
            _esc(os.getenv("APP_VERSION", "1.0")), _esc(os.getenv("PJSIP_VERSION", "")),
            _esc(self.proxy), _esc(self.username)))

        lines.append("# HELP gate_sip_registered SIP account registration state (1/0).")
        lines.append("# TYPE gate_sip_registered gauge")
        lines.append("gate_sip_registered %d" % reg)

        with m.lock:
            lines.append("# HELP gate_sip_registration_status_code Last REGISTER status code.")
            lines.append("# TYPE gate_sip_registration_status_code gauge")
            lines.append("gate_sip_registration_status_code %d" % m.reg_status)
            lines.append("# HELP gate_sip_last_register_ok_timestamp_seconds Unix time of last 200 REGISTER.")
            lines.append("# TYPE gate_sip_last_register_ok_timestamp_seconds gauge")
            lines.append("gate_sip_last_register_ok_timestamp_seconds %.0f" % m.reg_last_ok_ts)
            lines.append("# HELP gate_sip_registrations_total Successful REGISTERs.")
            lines.append("# TYPE gate_sip_registrations_total counter")
            lines.append("gate_sip_registrations_total %d" % m.reg_success_total)
            lines.append("# HELP gate_sip_register_failures_total Failed REGISTERs (>=300).")
            lines.append("# TYPE gate_sip_register_failures_total counter")
            lines.append("gate_sip_register_failures_total %d" % m.reg_failure_total)
            lines.append("# HELP gate_call_in_progress Whether a call is active now (1/0).")
            lines.append("# TYPE gate_call_in_progress gauge")
            lines.append("gate_call_in_progress %d" % m.in_progress)

            lines.append("# HELP gate_call_attempts_total Call attempts per target.")
            lines.append("# TYPE gate_call_attempts_total counter")
            for t, c in sorted(m.attempts.items()):
                lines.append('gate_call_attempts_total{target="%s"} %d' % (_esc(t), c))

            lines.append("# HELP gate_call_result_total Call outcomes per target.")
            lines.append("# TYPE gate_call_result_total counter")
            for (t, r), c in sorted(m.results.items()):
                lines.append('gate_call_result_total{target="%s",result="%s"} %d' % (_esc(t), _esc(r), c))

            lines.append("# HELP gate_last_sip_status_code Last SIP status code per target.")
            lines.append("# TYPE gate_last_sip_status_code gauge")
            for t, s in sorted(m.last_status.items()):
                lines.append('gate_last_sip_status_code{target="%s"} %d' % (_esc(t), s))

            lines.append("# HELP gate_last_call_reached Whether the last call reached the far end (got a provisional/answer).")
            lines.append("# TYPE gate_last_call_reached gauge")
            for t, v in sorted(m.last_reached.items()):
                lines.append('gate_last_call_reached{target="%s"} %d' % (_esc(t), v))

            lines.append("# HELP gate_last_call_duration_seconds Last call INVITE->disconnect duration per target.")
            lines.append("# TYPE gate_last_call_duration_seconds gauge")
            for t, v in sorted(m.last_duration_seconds.items()):
                lines.append('gate_last_call_duration_seconds{target="%s"} %.3f' % (_esc(t), v))

            lines.append("# HELP gate_last_call_ring_seconds Last call INVITE->first provisional per target.")
            lines.append("# TYPE gate_last_call_ring_seconds gauge")
            for t, v in sorted(m.last_ring_seconds.items()):
                lines.append('gate_last_call_ring_seconds{target="%s"} %.3f' % (_esc(t), v))

            lines.append("# HELP gate_last_call_timestamp_seconds Unix time of the last attempt per target.")
            lines.append("# TYPE gate_last_call_timestamp_seconds gauge")
            for t, v in sorted(m.last_ts.items()):
                lines.append('gate_last_call_timestamp_seconds{target="%s"} %.0f' % (_esc(t), v))

            lines.append("# HELP gate_call_provisional_total Provisional responses (100/180/183) received per target+code — proves the trunk answered the INVITE.")
            lines.append("# TYPE gate_call_provisional_total counter")
            for (t, code), c in sorted(m.provisional.items()):
                lines.append('gate_call_provisional_total{target="%s",code="%d"} %d' % (_esc(t), code, c))

            lines.append("# HELP gate_last_call_first_response_code First provisional SIP code of the last call per target (0=trunk never answered).")
            lines.append("# TYPE gate_last_call_first_response_code gauge")
            for t, v in sorted(m.first_response.items()):
                lines.append('gate_last_call_first_response_code{target="%s"} %d' % (_esc(t), v))

        return ("\n".join(lines) + "\n").encode()
