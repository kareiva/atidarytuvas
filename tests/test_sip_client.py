"""Unit tests for sip_client pure logic + metrics (pjsua2 stubbed in conftest)."""
import pjsua2 as pj  # the stub from conftest
import sip_client as sc


# ---------------------------------------------------------------- classify_result
class TestClassifyResult:
    def test_answered_wins(self):
        # answered takes precedence regardless of other flags
        assert sc.classify_result(True, True, True, 200) == "answered"
        assert sc.classify_result(True, False, False, 0) == "answered"

    def test_no_response_when_we_cancel_and_never_reached(self):
        # The GATE/DOOR/TEST symptom: we auto-hang-up, trunk sent no provisional.
        assert sc.classify_result(False, True, False, 487) == "no_response"
        assert sc.classify_result(False, True, False, 0) == "no_response"

    def test_cancelled_when_we_cancel_after_ringing(self):
        # Reached the far end (got a provisional) then we cancelled: normal open.
        assert sc.classify_result(False, True, True, 487) == "cancelled"
        assert sc.classify_result(False, True, True, 0) == "cancelled"
        assert sc.classify_result(False, True, True, 200) == "cancelled"

    def test_rejected_when_provider_ends_with_4xx_plus_and_not_our_cancel(self):
        assert sc.classify_result(False, False, True, 486) == "rejected"   # Busy
        assert sc.classify_result(False, False, False, 603) == "rejected"  # Decline
        assert sc.classify_result(False, False, True, 404) == "rejected"

    def test_rejected_even_if_reached_but_we_did_not_cancel(self):
        # 480 after ringing, we did not initiate hangup -> rejected
        assert sc.classify_result(False, False, True, 480) == "rejected"

    def test_ended_normal_sub_400_not_our_cancel(self):
        assert sc.classify_result(False, False, True, 200) == "ended"
        assert sc.classify_result(False, False, True, 0) == "ended"


# ---------------------------------------------------------------- _esc
class TestEsc:
    def test_plain(self):
        assert sc._esc("GATE") == "GATE"

    def test_escapes_quotes_backslash_newline(self):
        assert sc._esc('a"b') == 'a\\"b'
        assert sc._esc("a\\b") == "a\\\\b"
        assert sc._esc("a\nb") == "a b"

    def test_non_string(self):
        assert sc._esc(487) == "487"


# ---------------------------------------------------------------- _state_name
class TestStateName:
    def test_known_states(self):
        assert sc._state_name(pj, pj.PJSIP_INV_STATE_CALLING) == "CALLING"
        assert sc._state_name(pj, pj.PJSIP_INV_STATE_DISCONNECTED) == "DISCONNECTED"
        assert sc._state_name(pj, pj.PJSIP_INV_STATE_CONFIRMED) == "CONFIRMED"

    def test_unknown_state(self):
        assert sc._state_name(pj, 999) == "999"


# ---------------------------------------------------------------- CallMetrics
class TestCallMetrics:
    def test_record_attempt_resets_per_call_fields(self):
        m = sc.CallMetrics()
        m.last_reached["GATE"] = 1
        m.first_response["GATE"] = 180
        m.record_attempt("GATE")
        assert m.attempts["GATE"] == 1
        assert m.last_reached["GATE"] == 0
        assert m.first_response["GATE"] == 0
        assert m.last_ts["GATE"] > 0

    def test_attempts_accumulate(self):
        m = sc.CallMetrics()
        m.record_attempt("GATE")
        m.record_attempt("GATE")
        m.record_attempt("DOOR")
        assert m.attempts == {"GATE": 2, "DOOR": 1}

    def test_record_result_counts_and_fields(self):
        m = sc.CallMetrics()
        m.record_result("GATE", "no_response", status=487, reason="Request Terminated",
                        duration_seconds=10.0, reached=False)
        assert m.results[("GATE", "no_response")] == 1
        assert m.last_status["GATE"] == 487
        assert m.last_reason["GATE"] == "Request Terminated"
        assert m.last_duration_seconds["GATE"] == 10.0
        assert m.last_reached["GATE"] == 0
        # final-status distribution + current-result info are also recorded
        assert m.final_status[("GATE", 487)] == 1
        assert m.last_result["GATE"] == "no_response"

    def test_final_status_distribution_accumulates(self):
        m = sc.CallMetrics()
        m.record_result("GATE", "no_response", status=487)
        m.record_result("GATE", "rejected", status=486)
        m.record_result("GATE", "no_response", status=487)
        assert m.final_status[("GATE", 487)] == 2
        assert m.final_status[("GATE", 486)] == 1
        assert m.last_result["GATE"] == "no_response"

    def test_provisional_and_first_response(self):
        m = sc.CallMetrics()
        m.record_attempt("DOOR")
        m.record_provisional("DOOR", 100)
        m.record_provisional("DOOR", 180)
        m.record_provisional("DOOR", 180)
        assert m.provisional[("DOOR", 100)] == 1
        assert m.provisional[("DOOR", 180)] == 2
        # first_response is the FIRST provisional seen, not overwritten
        assert m.first_response["DOOR"] == 100

    def test_mark_reached_sets_once(self):
        m = sc.CallMetrics()
        m.mark_reached("GATE", 1.5)
        m.mark_reached("GATE", 9.9)
        assert m.last_reached["GATE"] == 1
        assert m.last_ring_seconds["GATE"] == 1.5  # not overwritten by the 2nd

    def test_registration_success_and_failure(self):
        m = sc.CallMetrics()
        m.record_registration(200)
        m.record_registration(200)
        m.record_registration(401)   # <300 branch? 401 >=300 -> failure
        m.record_registration(403)
        assert m.reg_status == 403
        assert m.reg_success_total == 2
        assert m.reg_failure_total == 2
        assert m.reg_last_ok_ts > 0

    def test_in_progress_toggle(self):
        m = sc.CallMetrics()
        assert m.in_progress == 0
        m.set_in_progress(True)
        assert m.in_progress == 1
        m.set_in_progress(False)
        assert m.in_progress == 0


# ---------------------------------------------------------------- render_metrics
def _client():
    # Build a SIPClient without starting PJSIP; render_metrics reads metrics +
    # is_registered (account is None -> False).
    return sc.SIPClient("sips.peoplefone.lt:5060", "90852528635", "secret",
                        app_logger=_NullLogger())


class _NullLogger:
    def info(self, *a): pass
    def error(self, *a): pass
    def call_event(self, *a): pass
    def sip_request(self, *a): pass
    def sip_response(self, *a): pass
    def registration_event(self, *a): pass


class TestRenderMetrics:
    def test_contains_core_series(self):
        c = _client()
        out = c.render_metrics().decode()
        assert "gate_sip_registered 0" in out          # account not started
        assert "gate_build_info{" in out
        assert 'username="90852528635"' in out
        assert "gate_sip_registration_status_code 0" in out
        assert "gate_call_in_progress 0" in out
        # valid prometheus exposition: every non-comment line has a value
        for line in out.splitlines():
            if line and not line.startswith("#"):
                assert " " in line

    def test_reflects_recorded_calls(self):
        c = _client()
        c.metrics.record_attempt("GATE")
        c.metrics.record_result("GATE", "no_response", status=487, reached=False,
                                duration_seconds=10.0)
        c.metrics.record_registration(200)
        out = c.render_metrics().decode()
        assert 'gate_call_attempts_total{target="GATE"} 1' in out
        assert 'gate_call_result_total{target="GATE",result="no_response"} 1' in out
        assert 'gate_last_sip_status_code{target="GATE"} 487' in out
        assert "gate_sip_registrations_total 1" in out
        assert 'gate_call_final_status_total{target="GATE",code="487"} 1' in out
        assert 'gate_last_call_result{target="GATE",result="no_response"} 1' in out

    def test_label_escaping_in_output(self):
        c = _client()
        c.metrics.record_attempt('weird"name')
        out = c.render_metrics().decode()
        assert 'target="weird\\"name"' in out
