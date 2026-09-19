"""
smart_contract.py
-----------------
Smart Contract definitions for the VANET blockchain.

Key contract:  IntrusionDetectionContract
  – Implements BSM validation rules as a deployable smart contract
    rather than hard-coded checks, matching the paper's architecture.
"""

import json


class SmartContract:
    """Base smart contract with code (rule identifier) and mutable state."""

    def __init__(self, code, state=None):
        self.code = code
        self.state = state or {}

    def execute(self, transaction_data):
        """
        Override in subclasses to implement contract logic.

        Returns
        -------
        (bool, str)  –  (is_valid, reason)
        """
        raise NotImplementedError

    def to_dict(self):
        return {
            "code": self.code,
            "state": self.state,
        }


class IntrusionDetectionContract(SmartContract):
    """
    Smart contract that validates BSM data against safety thresholds.

    Thresholds are part of the contract *state* so they can be updated
    on-chain without redeploying the contract, just like a real
    blockchain smart contract.
    """

    DEFAULT_THRESHOLDS = {
        "max_speed": 200,            # km/h
        "max_acceleration": 10,      # m/s^2  (absolute)
        "min_heading": 0,
        "max_heading": 360,
        "min_latitude": -90,
        "max_latitude": 90,
        "min_longitude": -180,
        "max_longitude": 180,
    }

    def __init__(self, thresholds=None):
        state = {**self.DEFAULT_THRESHOLDS, **(thresholds or {})}
        super().__init__(code="IntrusionDetectionContract_v1", state=state)
        self.blocked_nodes = set()
        self.alert_log = []

    # ------------------------------------------------------------------ #
    # Core validation logic (the "contract execution")
    # ------------------------------------------------------------------ #
    def execute(self, transaction_data):
        """
        Validate a BSM transaction against the on-chain thresholds.

        Parameters
        ----------
        transaction_data : dict
            Must contain key ``bsm_data`` with BSM fields.

        Returns
        -------
        (bool, str)  –  (passed, reason)
        """
        bsm = transaction_data.get("bsm_data", {})
        sender = transaction_data.get("sender", "unknown")
        reasons = []

        # --- Speed check ---
        speed = bsm.get("speed", 0)
        if speed > self.state["max_speed"]:
            reasons.append(f"speed={speed} exceeds max {self.state['max_speed']}")

        # --- Acceleration check ---
        accel = bsm.get("acceleration", 0)
        if abs(accel) > self.state["max_acceleration"]:
            reasons.append(f"acceleration={accel} exceeds ±{self.state['max_acceleration']}")

        # --- Heading check ---
        heading = bsm.get("heading", 0)
        if not (self.state["min_heading"] <= heading <= self.state["max_heading"]):
            reasons.append(f"heading={heading} out of [{self.state['min_heading']}, {self.state['max_heading']}]")

        # --- Position check ---
        pos = bsm.get("position", {})
        lat = pos.get("latitude", 0)
        lon = pos.get("longitude", 0)
        if not (self.state["min_latitude"] <= lat <= self.state["max_latitude"]):
            reasons.append(f"latitude={lat} invalid")
        if not (self.state["min_longitude"] <= lon <= self.state["max_longitude"]):
            reasons.append(f"longitude={lon} invalid")

        if reasons:
            alert = {
                "sender": sender,
                "violations": reasons,
            }
            self.alert_log.append(alert)
            self.blocked_nodes.add(sender)
            return False, "; ".join(reasons)

        return True, "OK"

    def to_dict(self):
        base = super().to_dict()
        base["blocked_nodes"] = list(self.blocked_nodes)
        base["alert_count"] = len(self.alert_log)
        return base
