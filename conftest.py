"""Test bootstrap: stub the native `pjsua2` module so sip_client/app import
without the compiled PJSIP bindings (not present in a plain test env), and put
the project root on sys.path.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))

if "pjsua2" not in sys.modules:
    pj = types.ModuleType("pjsua2")

    class _Base:
        def __init__(self, *a, **k):
            pass

    # Subclassable base classes used by sip_client (SIPAccount/SIPCall).
    class Account(_Base):
        pass

    class Call(_Base):
        pass

    pj.Account = Account
    pj.Call = Call

    # INVITE session states (order matches PJSIP).
    for i, name in enumerate([
        "NULL", "CALLING", "INCOMING", "EARLY",
        "CONNECTING", "CONFIRMED", "DISCONNECTED",
    ]):
        setattr(pj, "PJSIP_INV_STATE_" + name, i)

    pj.PJSIP_TRANSPORT_UDP = 1

    # Config/util classes referenced at runtime (not exercised by unit tests).
    for n in ["Endpoint", "EpConfig", "LogConfig", "TransportConfig",
              "AccountConfig", "AuthCredInfo", "CallOpParam"]:
        setattr(pj, n, type(n, (_Base,), {}))

    sys.modules["pjsua2"] = pj
