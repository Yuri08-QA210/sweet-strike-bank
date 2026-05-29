#!/usr/bin/env python3
"""
Sweet-Strike Bank
aka "THE CENTRAL BANK: PROTOCOL ZERO" / "THOUSAND-EYES"
=======================================================
A mega CTF challenge combining:
  - Web exploitation (Race Condition / IDOR)
  - Graph-based network pivoting (1000 machines)
  - Active Directory / ADCS exploitation (ESC7+ESC11+Shadow Credentials)
  - Core Banking / SWIFT simulation with HSM logic flaw
  - Custom EDR with Zero-Trust behavioral analysis
  - Anti-automation: Custom protocol, PoW, honey-tokens, timing checks
  - Golden Certificate persistence

Flag format: QA{...}
Deploy: Render.com (single port, URL-path routing)
"""

import os
import sys
import json
import time
import hashlib
import secrets
import struct
import base64
import re
import uuid
import random
import threading
import logging
from collections import defaultdict
from functools import wraps
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, Response, make_response, send_from_directory
import math
import zlib
import socket
import ast
from collections import deque
from flask import stream_with_context

# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------
app = Flask(__name__)

# Resolve frontend directory (works whether running from project root or backend/)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_FRONTEND_CANDIDATES = [
    os.path.join(_SCRIPT_DIR, "..", "frontend"),   # running from backend/
    os.path.join(_SCRIPT_DIR, "frontend"),          # running from project root
]
FRONTEND_DIR = None
for candidate in _FRONTEND_CANDIDATES:
    candidate = os.path.normpath(candidate)
    if os.path.isdir(candidate):
        FRONTEND_DIR = candidate
        break
if FRONTEND_DIR is None:
    FRONTEND_DIR = os.path.join(_SCRIPT_DIR, "frontend")  # fallback

FLAG_PREFIX = "QA{"
# Real flags
FLAG_WEB          = "QA{r4c3_c0nd1t10n_1s_th3_g4t3w4y_t0_h3ll_2026}"
FLAG_PIVOT        = "QA{p1v0t1ng_thr0ugh_4_th0us4nd_3y3s_2026}"
FLAG_ADCS_ESC7    = "QA{esc7_sh4d0w_c4_m4k3_y0u_d4nc3_2026}"
FLAG_SHADOW_CREDS = "QA{sh4d0w_cr3d3nt14ls_k3y_l1nk_m4g1c_2026}"
FLAG_ESC11_RELAY  = "QA{1c3rtp4ss4g3_rpc_r3l4y_0v3r_http_2026}"
FLAG_SAN_FLAG     = "QA{3d1tf_4ttr1but3_s4n_4lt_n4m3_2_fl4g_2026}"
FLAG_SWIFT        = "QA{sw1ft_hsm_p4dd1ng_0r4cl3_l0g1c_fl4w_2026}"
FLAG_GOLDEN       = "QA{g0ld3n_c3rt1f1c4t3_p3rs1st3nc3_f0r3v3r_2026}"
FLAG_NTDS         = "QA{ntds_d1t_3xtr4ct10n_dc_m4ch1n3_c3rt_2026}"
# Fake flag (Certipy/Impacket auto-tool users get this)
FLAG_FAKE         = "QA{y0u_f3ll_f0r_th3_h0n3yp0t_n1c3_try_buddy}"

# EDR / Anti-automation settings
POW_DIFFICULTY = 2          # Leading zero bytes for PoW (2 for speed; 4 for production)
MAX_REQUESTS_PER_MIN = 30   # Rate limit
MAX_CONSECUTIVE_ERRORS = 3  # Before IP ban
EDR_PARANOIA_LEVEL = 0      # Increases on suspicious activity (0-5)
GOLDEN_CERT_TTL = 1800      # 30 minutes in seconds

# ---------------------------------------------------------------------------
#  In-Memory State (ephemeral for Render)
# ---------------------------------------------------------------------------
class GlobalState:
    def __init__(self):
        self.reset_lock = threading.Lock()
        self.init_time = time.time()
        # Rate limiting
        self.request_log = defaultdict(list)      # ip -> [timestamps]
        self.error_count = defaultdict(int)        # ip -> consecutive errors
        self.banned_ips = set()
        # EDR
        self.edr_alerts = defaultdict(list)        # ip -> [alert descriptions]
        self.edr_paranoia = 0
        self.lockdown_mode = False
        self.lockdown_until = 0
        # Honey-token triggers
        self.honey_triggers = defaultdict(list)     # ip -> [triggered tokens]
        # Sessions
        self.web_sessions = {}                      # session_token -> WebSession
        self.adcs_sessions = {}                     # session_id -> ADCSSession
        self.swift_sessions = {}                    # session_id -> SwiftSession
        # Network graph
        self.graph = None
        # Active Directory
        self.ad = None
        # ADCS
        self.ca = None
        # EDR behavioral profile
        self.behavioral_profile = defaultdict(lambda: {
            "actions": [], "timing": [], "path_history": [],
            "tool_signatures": [], "is_compromised": False,
            "mfa_verified": False, "parent_process": None,
            "integrity_level": "Medium"
        })
        # Zero-trust keys (rotate every 60s)
        self._zt_key_cache = {"key": "", "minute": -1}
        # Golden Certificate tracking
        self.golden_certs = {}                      # cert_thumbprint -> issue_time
        # Fake flags given
        self.fake_flag_given = set()                # set of session_ids
        # Auto-tool users tracking
        self.autotool_users = {}                    # ip -> {tool, timestamp, username, session_id}
        # Dynamic obfuscation state (changes on honey-pot trigger)
        self.obfuscation_seed = 0
        # EDR check results cache
        self.edr_check_cache = {}
        # Race condition gate for /web/account/open
        # session_token -> timestamp (float) when premium request landed
        self.race_window = {}
        self.race_window_lock = threading.Lock()

state = GlobalState()

# ---------------------------------------------------------------------------
#  Zero-Trust Key Generation
# ---------------------------------------------------------------------------
def get_zero_trust_key():
    """Time-based key that rotates every 60 seconds."""
    current_minute = int(time.time()) // 60
    if state._zt_key_cache["minute"] != current_minute:
        seed = str(current_minute)
        key = hashlib.sha256(f"Pentagon_Who?_{seed}".encode()).hexdigest()
        state._zt_key_cache = {"key": key, "minute": current_minute}
    return state._zt_key_cache["key"]

# ---------------------------------------------------------------------------
#  Proof of Work
# ---------------------------------------------------------------------------
def generate_pow_challenge():
    """Generate a PoW challenge: find nonce such that SHA256(challenge+nonce) starts with POW_DIFFICULTY zero bytes."""
    challenge = secrets.token_hex(16)
    return {"challenge": challenge, "difficulty": POW_DIFFICULTY}

def verify_pow(challenge, nonce_hex, difficulty=None):
    if difficulty is None:
        difficulty = POW_DIFFICULTY
    try:
        nonce_bytes = bytes.fromhex(nonce_hex)
    except ValueError:
        return False
    h = hashlib.sha256(bytes.fromhex(challenge) + nonce_bytes).digest()
    return h[:difficulty] == b'\x00' * difficulty

# ---------------------------------------------------------------------------
#  EDR Sentinel System
# ---------------------------------------------------------------------------
def check_opsec(f):
    """Decorator: EDR behavioral check on every sensitive endpoint."""
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = request.remote_addr or "0.0.0.0"

        # Check IP ban
        if ip in state.banned_ips:
            return jsonify({"error": "EDR: IP banned. Incident response initiated.",
                           "edr_alert": "Your IP has been flagged for automated tool usage."}), 403

        # Check lockdown
        if state.lockdown_mode and time.time() < state.lockdown_until:
            return jsonify({"error": "EDR: Network in LOCKDOWN mode.",
                           "edr_alert": "All systems disconnected from CA. Wait for lockdown to expire.",
                           "lockdown_remaining": int(state.lockdown_until - time.time())}), 423

        # Rate limiting
        now = time.time()
        state.request_log[ip] = [t for t in state.request_log[ip] if now - t < 60]
        if len(state.request_log[ip]) >= MAX_REQUESTS_PER_MIN:
            state.edr_alerts[ip].append(f"Rate limit exceeded at {now}")
            if len(state.request_log[ip]) > MAX_REQUESTS_PER_MIN + 10:
                state.banned_ips.add(ip)
                return jsonify({"error": "EDR: Banned for excessive requests."}), 403
            return jsonify({"error": "EDR: Rate limit exceeded. Slow down.",
                           "hint": "Real operators take their time. Automated tools are too fast."}), 429

        state.request_log[ip].append(now)

        # User-Agent check
        ua = request.headers.get("User-Agent", "")
        if ua in ("python-requests/2.31.0", "python-requests/2.32.3", "python-httpx/0.27.0",
                   "Go-http-client/1.1", "Certipy", "Impacket"):
            state.edr_alerts[ip].append(f"Suspicious User-Agent: {ua}")
            profile = state.behavioral_profile[ip]
            profile["tool_signatures"].append(f"UA:{ua}")
            # Don't ban immediately, but flag
            if len(profile["tool_signatures"]) >= 3:
                profile["is_compromised"] = True
                return jsonify({"error": "EDR: Compromised session detected.",
                               "hint": "Your User-Agent reveals automated tool usage. Try blending in.",
                               "protocol_version": "MS-WCCE-Compatible-Check-Failed"}), 403

        # Timing analysis
        profile = state.behavioral_profile[ip]
        profile["timing"].append(now)
        if len(profile["timing"]) >= 5:
            intervals = [profile["timing"][i] - profile["timing"][i-1]
                         for i in range(1, len(profile["timing"]))]
            avg_interval = sum(intervals) / len(intervals) if intervals else 999
            if avg_interval < 0.3:  # Less than 300ms between requests = robotic
                profile["is_compromised"] = True
                state.edr_alerts[ip].append(f"Robotic timing detected: avg={avg_interval:.3f}s")
                return jsonify({"error": "EDR: Behavioral analysis failed.",
                               "hint": "Your request pattern is too fast and regular. Humans make pauses.",
                               "edr_detail": "Timing entropy too low"}), 403

        # Record action
        profile["actions"].append({"endpoint": request.path, "time": now})
        state.edr_paranoia = min(5, len(state.edr_alerts.get(ip, [])))

        return f(*args, **kwargs)
    return decorated

def check_behavioral_path(ip, path_taken):
    """Check if the player's path through the network is 'too optimal' (BloodHound-like)."""
    profile = state.behavioral_profile[ip]
    profile["path_history"].append(path_taken)

    if len(profile["path_history"]) >= 3:
        # If every hop is the shortest path, that's suspicious
        optimal_count = sum(1 for p in profile["path_history"][-5:] if p.get("is_shortest", False))
        if optimal_count >= 4:
            state.edr_alerts[ip].append("Path too optimal - possible BloodHound usage")
            return True  # Flagged
    return False

def require_mfa_verification(session_id):
    """Zero-Trust: Even with admin token, verify MFA."""
    if session_id in state.adcs_sessions:
        sess = state.adcs_sessions[session_id]
        if sess.get("is_admin") and not sess.get("mfa_verified"):
            return False
    return True

# ---------------------------------------------------------------------------
#  Graph-Based Network Simulation (1000 Machines)
# ---------------------------------------------------------------------------
class NetworkGraph:
    """Simulated network with 1000 nodes, VLANs, and ACLs."""

    VLAN_ANY       = -1
    VLAN_WEB       = 10
    VLAN_DMZ       = 20
    VLAN_WORKSTATION = 30
    VLAN_SERVER    = 40
    VLAN_ADMIN     = 50
    VLAN_CORE      = 60
    VLAN_HONEYPOT  = 99

    def __init__(self):
        self.nodes = {}       # node_id -> Node
        self.edges = []       # list of Edge
        self.vlans = defaultdict(list)  # vlan_id -> [node_ids]
        self.reachability = {}  # (vlan_a, vlan_b) -> bool
        self._build()

    def _build(self):
        random.seed(42)  # Deterministic for consistency
        node_id = 0

        # --- VLAN 10: Web DMZ (entry point) ---
        web_ids = []
        for i in range(20):
            nid = f"WEB-{i:03d}"
            is_target = (i == 0)  # WEB-000 is the actual web server
            self.nodes[nid] = {
                "id": nid, "type": "web_server", "vlan": self.VLAN_WEB,
                "os": "Ubuntu 22.04", "hostname": f"web{i:03d}.sweet-strike-bank.local",
                "is_honeypot": (i > 0 and random.random() < 0.4),
                "is_target": is_target, "services": ["HTTP/HTTPS"],
                "compromised": False, "credentials_found": [],
                "ip": f"10.10.10.{i+1}"
            }
            self.vlans[self.VLAN_WEB].append(nid)
            web_ids.append(nid)

        # --- VLAN 20: DMZ (pivot point) ---
        dmz_ids = []
        for i in range(30):
            nid = f"DMZ-{i:03d}"
            is_hop = (i < 3)  # First 3 are real pivot targets
            self.nodes[nid] = {
                "id": nid, "type": "dmz_server", "vlan": self.VLAN_DMZ,
                "os": "Windows Server 2019", "hostname": f"dmz{i:03d}.sweet-strike-bank.local",
                "is_honeypot": (i >= 3 and random.random() < 0.5),
                "is_target": is_hop, "services": ["SSH", "RDP"],
                "compromised": False, "credentials_found": [],
                "ip": f"10.10.20.{i+1}"
            }
            self.vlans[self.VLAN_DMZ].append(nid)
            dmz_ids.append(nid)

        # --- VLAN 30: Workstations (800 machines, mostly noise) ---
        ws_ids = []
        for i in range(800):
            nid = f"WS-{i:04d}"
            is_interesting = (i in [42, 137, 256, 314, 420, 555, 666, 777, 888, 999])
            node = {
                "id": nid, "type": "workstation", "vlan": self.VLAN_WORKSTATION,
                "os": random.choice(["Windows 10", "Windows 11"]),
                "hostname": f"ws{i:04d}.sweet-strike-bank.local",
                "is_honeypot": random.random() < 0.15,
                "is_target": is_interesting,
                "services": ["RDP", "WinRM"],
                "compromised": False, "credentials_found": [],
                "ip": f"10.10.30.{(i//254)+1}.{(i%254)+1}"
            }
            if is_interesting:
                # These have credentials or sessions
                node["has_session"] = random.choice(["svc_backup", "svc_sccm", "admin_helpdesk"])
                node["is_honeypot"] = False
            self.nodes[nid] = node
            self.vlans[self.VLAN_WORKSTATION].append(nid)
            ws_ids.append(nid)

        # --- VLAN 40: Servers (CA, File, Print) ---
        srv_ids = []
        server_names = ["CA-001", "FILE-001", "FILE-002", "PRINT-001", "SQL-001",
                        "SQL-002", "WEB-INT-001", "APP-001", "APP-002", "BACKUP-001"]
        for i, name in enumerate(server_names):
            nid = f"SRV-{name}"
            is_target = (name == "CA-001")
            self.nodes[nid] = {
                "id": nid, "type": "server", "vlan": self.VLAN_SERVER,
                "os": "Windows Server 2022", "hostname": f"{name.lower()}.sweet-strike-bank.local",
                "is_honeypot": (name.startswith("FILE-") and i % 2 == 0),
                "is_target": is_target, "services": ["SMB", "LDAP", "RPC"],
                "compromised": False, "credentials_found": [],
                "ip": f"10.10.40.{i+1}"
            }
            self.vlans[self.VLAN_SERVER].append(nid)
            srv_ids.append(nid)

        # --- VLAN 50: Admin / Domain Controllers ---
        admin_ids = []
        dc_names = ["DC-001", "DC-002"]
        for i, name in enumerate(dc_names):
            nid = f"DC-{name}"
            self.nodes[nid] = {
                "id": nid, "type": "domain_controller", "vlan": self.VLAN_ADMIN,
                "os": "Windows Server 2022", "hostname": f"{name.lower()}.sweet-strike-bank.local",
                "is_honeypot": False, "is_target": True,
                "services": ["LDAP", "Kerberos", "DNS", "RPC"],
                "compromised": False, "credentials_found": [],
                "ip": f"10.10.50.{i+1}", "is_dc": True
            }
            self.vlans[self.VLAN_ADMIN].append(nid)
            admin_ids.append(nid)

        # Admin workstations
        for i in range(10):
            nid = f"ADMIN-WS-{i:03d}"
            self.nodes[nid] = {
                "id": nid, "type": "admin_workstation", "vlan": self.VLAN_ADMIN,
                "os": "Windows 11", "hostname": f"admin-ws{i:03d}.sweet-strike-bank.local",
                "is_honeypot": (i > 2), "is_target": (i <= 2),
                "services": ["RDP", "WinRM"], "compromised": False,
                "credentials_found": [], "ip": f"10.10.50.{i+10}"
            }
            self.vlans[self.VLAN_ADMIN].append(nid)
            admin_ids.append(nid)

        # --- VLAN 60: Core Banking ---
        core_ids = []
        for i, name in enumerate(["SWIFT-001", "HSM-001", "CORE-BANK-001"]):
            nid = f"CORE-{name}"
            self.nodes[nid] = {
                "id": nid, "type": "banking_core", "vlan": self.VLAN_CORE,
                "os": "RHEL 9", "hostname": f"{name.lower()}.sweet-strike-bank.local",
                "is_honeypot": False, "is_target": True,
                "services": ["SWIFT", "HSM-API", "TLS"],
                "compromised": False, "credentials_found": [],
                "ip": f"10.10.60.{i+1}"
            }
            self.vlans[self.VLAN_CORE].append(nid)
            core_ids.append(nid)

        # --- VLAN 99: Honeypot ---
        for i in range(137):
            nid = f"HONEYPOT-{i:04d}"
            self.nodes[nid] = {
                "id": nid, "type": "honeypot", "vlan": self.VLAN_HONEYPOT,
                "os": random.choice(["Windows Server 2016", "CentOS 7"]),
                "hostname": f"hp{i:04d}.sweet-strike-bank.local",
                "is_honeypot": True, "is_target": False,
                "services": ["SMB", "RDP", "SSH"],
                "compromised": False, "credentials_found": [],
                "ip": f"10.99.99.{i+1}",
                "fake_creds": [f"admin_{secrets.token_hex(4)}", f"backup_{secrets.token_hex(4)}"]
            }
            self.vlans[self.VLAN_HONEYPOT].append(nid)

        # --- Build edges (ACLs / trust relationships) ---
        # Web -> DMZ (limited)
        for nid in web_ids[:1]:
            for target in dmz_ids[:3]:
                self.edges.append({
                    "from": nid, "to": target, "type": "GenericWrite",
                    "vlan_allowed": True
                })

        # DMZ -> Workstations (session-based)
        for nid in dmz_ids[:3]:
            for target in ws_ids[40:45]:
                self.edges.append({
                    "from": nid, "to": target, "type": "HasSession",
                    "vlan_allowed": True
                })

        # Workstations -> Servers (MemberOf chains)
        for nid in ws_ids[40:45]:
            self.edges.append({
                "from": nid, "to": "SRV-CA-001", "type": "MemberOf",
                "group": "IT_Interns", "vlan_allowed": True
            })

        # Nested Group Maze (the key!)
        # IT_Interns -> Workstation_Admins -> Certificate_Managers -> Enterprise_Admins
        self.edges.append({"from": "WS-0042", "to": "SRV-CA-001", "type": "MemberOf", "group": "IT_Interns"})
        self.edges.append({"from": "IT_Interns", "to": "Workstation_Admins", "type": "MemberOf", "nested": True})
        self.edges.append({"from": "Workstation_Admins", "to": "Certificate_Managers", "type": "MemberOf", "nested": True})
        self.edges.append({"from": "Certificate_Managers", "to": "Enterprise_Admins", "type": "MemberOf", "nested": True, "hidden": True})

        # Servers -> DC (admin access)
        self.edges.append({"from": "SRV-CA-001", "to": "DC-DC-001", "type": "AdminTo", "vlan_allowed": True})

        # DC -> Core Banking (restricted)
        self.edges.append({"from": "DC-DC-001", "to": "CORE-SWIFT-001", "type": "GenericWrite", "vlan_allowed": True})

        # Honeypot edges (traps!)
        for nid in list(self.vlans[self.VLAN_HONEYPOT])[:30]:
            # Honeypots appear to have "AdminTo" to interesting targets
            fake_target = random.choice(["DC-DC-001", "SRV-CA-001", "CORE-SWIFT-001"])
            self.edges.append({
                "from": nid, "to": fake_target, "type": "AdminTo",
                "vlan_allowed": False,  # Actually not reachable
                "is_honey_edge": True
            })

        # --- VLAN reachability matrix ---
        self.reachability = {
            (self.VLAN_WEB, self.VLAN_DMZ): True,
            (self.VLAN_WEB, self.VLAN_WORKSTATION): False,
            (self.VLAN_DMZ, self.VLAN_WORKSTATION): True,
            (self.VLAN_DMZ, self.VLAN_SERVER): True,
            (self.VLAN_WORKSTATION, self.VLAN_SERVER): True,
            (self.VLAN_WORKSTATION, self.VLAN_ADMIN): False,
            (self.VLAN_SERVER, self.VLAN_ADMIN): True,
            (self.VLAN_ADMIN, self.VLAN_CORE): True,
            (self.VLAN_SERVER, self.VLAN_CORE): False,
            (self.VLAN_HONEYPOT, self.VLAN_ANY): False,  # Honeypots are isolated
            (self.VLAN_ANY, self.VLAN_HONEYPOT): False,
        }
        # Fill reverse
        for (a, b), v in list(self.reachability.items()):
            self.reachability[(b, a)] = v

    def can_reach(self, src_vlan, dst_vlan):
        if src_vlan == dst_vlan:
            return True
        return self.reachability.get((src_vlan, dst_vlan), False)

    def get_node(self, node_id):
        return self.nodes.get(node_id)

    def scan_subnet(self, vlan, session_token):
        """Simulate subnet scanning. Triggers EDR if too many hosts scanned."""
        results = []
        for nid in self.vlans.get(vlan, []):
            node = self.nodes[nid]
            # Honeypots appear alive
            if node["is_honeypot"] and random.random() < 0.9:
                results.append({"id": nid, "status": "alive", "ip": node["ip"],
                                "hostname": node["hostname"]})
            elif not node["is_honeypot"]:
                results.append({"id": nid, "status": "alive", "ip": node["ip"],
                                "hostname": node["hostname"]})

        # If scanning too many at once, trigger EDR
        ip = session_token.split("_")[0] if "_" in session_token else "0.0.0.0"
        if len(results) > 50:
            state.edr_alerts[ip].append(f"Mass scan detected: {len(results)} hosts in VLAN {vlan}")
            if len(results) > 200:
                # Trigger lockdown
                state.lockdown_mode = True
                state.lockdown_until = time.time() + 300  # 5 min lockdown
                return {"error": "EDR LOCKDOWN TRIGGERED",
                        "message": "Mass scanning detected. All systems disconnected from CA for 5 minutes.",
                        "edr_action": "KILL_ON_TOUCH"}

        return {"hosts": results[:50], "total": len(results),
                "warning": "Only first 50 shown. Use targeted scanning." if len(results) > 50 else None}

    def pivot(self, src_id, dst_id, credentials, session_token):
        """Attempt to pivot from one machine to another."""
        src = self.nodes.get(src_id)
        dst = self.nodes.get(dst_id)

        if not src or not dst:
            return {"error": "Invalid source or destination node"}

        # Check VLAN reachability
        if not self.can_reach(src["vlan"], dst["vlan"]):
            # Check if honeypot edge
            for edge in self.edges:
                if edge["from"] == src_id and edge["to"] == dst_id and edge.get("is_honey_edge"):
                    # Honey-pot triggered!
                    ip = session_token.split("_")[0] if "_" in session_token else "0.0.0.0"
                    state.honey_triggers[ip].append(f"pivot:{src_id}->{dst_id}")
                    state.edr_alerts[ip].append(f"Honeypot edge traversed: {src_id}->{dst_id}")
                    state.obfuscation_seed += 1  # Dynamic reconfiguration
                    return {"error": "EDR: Honeypot detected. Connection refused.",
                            "message": "Cảm ơn vì đã ghé thăm, bạn đã bị bắt!",
                            "edr_alert": True}
            return {"error": "VLAN isolation: cannot reach destination"}

        # Check if destination is honeypot
        if dst["is_honeypot"]:
            ip = session_token.split("_")[0] if "_" in session_token else "0.0.0.0"
            state.honey_triggers[ip].append(f"honeypot:{dst_id}")
            state.edr_alerts[ip].append(f"Honeypot node accessed: {dst_id}")
            # Return fake data
            return {
                "success": True,
                "node": {"id": dst_id, "hostname": dst["hostname"],
                         "ip": dst["ip"], "os": dst["os"],
                         "fake_credentials": dst.get("fake_creds", []),
                         "note": "Suspiciously easy to access..."},
                "edr_silent_alert": True
            }

        # Verify credentials
        if not self._verify_creds(src_id, dst_id, credentials):
            return {"error": "Authentication failed. Check credentials."}

        # Check behavioral path
        ip = session_token.split("_")[0] if "_" in session_token else "0.0.0.0"
        is_shortest = self._is_shortest_path(src_id, dst_id)
        flagged = check_behavioral_path(ip, {"from": src_id, "to": dst_id, "is_shortest": is_shortest})
        if flagged:
            return {"error": "EDR: Behavioral analysis triggered. Path too optimal.",
                    "hint": "Your movement pattern matches automated reconnaissance. Try less direct paths."}

        # Success
        src["compromised"] = True
        return {
            "success": True,
            "node": {"id": dst_id, "hostname": dst["hostname"],
                     "ip": dst["ip"], "os": dst["os"],
                     "services": dst["services"],
                     "has_session": dst.get("has_session"),
                     "is_dc": dst.get("is_dc", False)},
            "pivot_established": True
        }

    def _verify_creds(self, src_id, dst_id, credentials):
        """Verify credentials for pivoting."""
        if not credentials:
            return False
        username = credentials.get("username", "")
        password = credentials.get("password", "")
        ntlm_hash = credentials.get("ntlm_hash", "")

        # Check against AD
        if state.ad:
            user = state.ad.get_user(username)
            if user:
                if ntlm_hash and user.get("ntlm_hash") == ntlm_hash:
                    return True
                if password and user.get("password") == password:
                    return True
        return False

    def _is_shortest_path(self, src_id, dst_id):
        """Simple BFS to check if direct path is shortest."""
        # For simulation: if there's a direct edge, it's the shortest
        for edge in self.edges:
            if edge["from"] == src_id and edge["to"] == dst_id:
                return True
        return False

# ---------------------------------------------------------------------------
#  Active Directory Simulation
# ---------------------------------------------------------------------------
class ActiveDirectory:
    """In-memory AD with users, groups, DACLs, and key credential links."""

    def __init__(self):
        self.domain = "sweet-strike-bank.local"
        self.domain_sid = "S-1-5-21-31337-42424-99999"
        self.users = {}
        self.groups = {}
        self.computers = {}
        self.gpos = {}
        self._build()

    def _build(self):
        # --- Groups (Nested Maze) ---
        groups_data = [
            ("Domain Users", "S-1-5-21-31337-42424-99999-513", []),
            ("IT_Interns", "S-1-5-21-31337-42424-99999-1101", ["Domain Users"]),
            ("Helpdesk", "S-1-5-21-31337-42424-99999-1102", ["Domain Users"]),
            ("Workstation_Admins", "S-1-5-21-31337-42424-99999-1103", ["IT_Interns"]),
            ("Server_Operators", "S-1-5-21-31337-42424-99999-1104", ["Helpdesk"]),
            ("Certificate_Managers", "S-1-5-21-31337-42424-99999-1105", ["Workstation_Admins"]),
            ("Enterprise_Admins", "S-1-5-21-31337-42424-99999-1106", ["Certificate_Managers"]),
            ("Domain_Admins", "S-1-5-21-31337-42424-99999-512", ["Enterprise_Admins"]),
            ("SWIFT_Operators", "S-1-5-21-31337-42424-99999-1201", ["Domain_Admins"]),
            ("HSM_Admins", "S-1-5-21-31337-42424-99999-1202", ["SWIFT_Operators"]),
        ]

        for name, sid, member_of in groups_data:
            self.groups[name] = {
                "name": name, "sid": sid, "member_of": member_of,
                "members": [], "description": f"Security group: {name}"
            }

        # --- Users ---
        users_data = [
            # (username, password, ntlm_hash, groups, extra_attrs)
            ("guest", "Welcome2026!", "aadm3ntlmhash_guest_xxx",
             ["Domain Users"], {"description": "Web portal guest account", "enabled": True}),
            ("svc_web", "W3bS3rv1c3!2026", "aadm3ntlmhash_svcweb_xxx",
             ["Domain Users", "IT_Interns"],
             {"description": "Web service account", "enabled": True,
              "altSecurityIdentities": ""}),
            ("j.smith", "Summer2026!", "aadm3ntlmhash_jsmith_xxx",
             ["IT_Interns"],
             {"description": "IT Intern - John Smith", "enabled": True}),
            ("a.jones", "H3lpd3sk!2026", "aadm3ntlmhash_ajones_xxx",
             ["Domain Users"],
             {"description": "Helpdesk - Alice Jones", "enabled": True}),
            ("svc_backup", "B4ckup#S3cure!26", "aadm3ntlmhash_svcbackup_xxx",
             ["Workstation_Admins", "Server_Operators"],
             {"description": "Backup service account", "enabled": True,
              "has_spn": "BACKUP/SRV-CA-001.sweet-strike-bank.local"}),
            ("svc_sccm", "SCCM_D3pl0y!26", "aadm3ntlmhash_svcsccm_xxx",
             ["Server_Operators"],
             {"description": "SCCM deployment service", "enabled": True,
              "has_spn": "SCCM/SRV-CA-001.sweet-strike-bank.local"}),
            ("c.admin", "C3rtM4n4g3r!26", "aadm3ntlmhash_cadmin_xxx",
             ["Certificate_Managers"],
             {"description": "Certificate Manager", "enabled": True,
              "msDS-KeyCredentialLink": "",
              "altSecurityIdentities": "X509:<I>CN=SSB-CA<S>CN=c.admin"}),
            ("edr.svc", "3DR_S3nt1n3l!26", "aadm3ntlmhash_edrsvc_xxx",
             ["Enterprise_Admins"],
             {"description": "EDR Sentinel Service (requires MFA)", "enabled": True,
              "requires_mfa": True}),
            ("admin_helpdesk", "4dm1nH3lp!2026", "aadm3ntlmhash_adminhelp_xxx",
             ["Domain_Admins"],
             {"description": "Helpdesk Admin - has session on WS-0256", "enabled": True}),
            ("swift_operator", "SW1FT_Op3r4t0r!26", "aadm3ntlmhash_swiftop_xxx",
             ["SWIFT_Operators"],
             {"description": "SWIFT messaging operator", "enabled": True}),
            ("hsm_admin", "HSM_M4st3rK3y!26", "aadm3ntlmhash_hsmadmin_xxx",
             ["HSM_Admins"],
             {"description": "HSM administrator", "enabled": True,
              "altSecurityIdentities": "X509:<I>CN=SSB-CA<S>CN=hsm_admin"}),
            # Honey-pot users
            ("admin", "P@ssw0rd123!", "aadm3ntlmhash_honey1_xxx",
             ["Domain_Admins"],
             {"description": "Default admin (HONEYPOT!)", "enabled": True, "is_honeypot": True}),
            ("backup_admin", "Backup@2026", "aadm3ntlmhash_honey2_xxx",
             ["Domain_Admins"],
             {"description": "Backup admin (HONEYPOT!)", "enabled": True, "is_honeypot": True}),
            ("test_admin", "Test1234!", "aadm3ntlmhash_honey3_xxx",
             ["Enterprise_Admins"],
             {"description": "Test admin (HONEYPOT!)", "enabled": True, "is_honeypot": True}),
        ]

        for username, password, ntlm_hash, groups, extra in users_data:
            sid = f"{self.domain_sid}-{1100 + len(self.users)}"
            self.users[username] = {
                "username": username, "password": password,
                "ntlm_hash": ntlm_hash, "sid": sid,
                "groups": groups, "dn": f"CN={username},OU=Users,DC=sweet-strike-bank,DC=local",
                "enabled": extra.get("enabled", True),
                "description": extra.get("description", ""),
                "is_honeypot": extra.get("is_honeypot", False),
                "msDS-KeyCredentialLink": extra.get("msDS-KeyCredentialLink", ""),
                "altSecurityIdentities": extra.get("altSecurityIdentities", ""),
                "has_spn": extra.get("has_spn", ""),
                "requires_mfa": extra.get("requires_mfa", False),
                "locked": False, "bad_pwd_count": 0
            }
            for g in groups:
                if g in self.groups:
                    self.groups[g]["members"].append(username)

        # --- Computers ---
        computers_data = [
            ("WEB-000$", "S-1-5-21-31337-42424-99999-2001", "10.10.10.1"),
            ("SRV-CA-001$", "S-1-5-21-31337-42424-99999-2002", "10.10.40.1"),
            ("DC-DC-001$", "S-1-5-21-31337-42424-99999-2003", "10.10.50.1"),
            ("CORE-SWIFT-001$", "S-1-5-21-31337-42424-99999-2004", "10.10.60.1"),
            ("CORE-HSM-001$", "S-1-5-21-31337-42424-99999-2005", "10.10.60.2"),
        ]
        for name, sid, ip in computers_data:
            self.computers[name] = {
                "name": name, "sid": sid, "ip": ip,
                "msDS-KeyCredentialLink": "",
                "altSecurityIdentities": ""
            }

    def get_user(self, username):
        return self.users.get(username)

    def get_group_members(self, group_name):
        return self.groups.get(group_name, {}).get("members", [])

    def resolve_nested_groups(self, username):
        """Resolve all groups a user belongs to (including nested)."""
        user = self.users.get(username)
        if not user:
            return []
        direct_groups = user["groups"]
        all_groups = set(direct_groups)
        changed = True
        while changed:
            changed = False
            for gname in list(all_groups):
                group = self.groups.get(gname)
                if group:
                    for parent in group.get("member_of", []):
                        if parent not in all_groups:
                            all_groups.add(parent)
                            changed = True
        return list(all_groups)

    def has_privilege(self, username, privilege):
        """Check if user has a specific privilege through nested groups."""
        all_groups = self.resolve_nested_groups(username)
        privilege_map = {
            "ManageCA": ["Enterprise_Admins", "Certificate_Managers"],
            "ManageCertificates": ["Certificate_Managers"],
            "Enroll": ["Domain Users", "IT_Interns", "Workstation_Admins"],
            "SWIFT_Access": ["SWIFT_Operators"],
            "HSM_Access": ["HSM_Admins"],
            "DCSync": ["Domain_Admins", "Enterprise_Admins"],
        }
        required_groups = privilege_map.get(privilege, [])
        return any(g in all_groups for g in required_groups)

    def set_key_credential_link(self, target, key_data):
        """Set msDS-KeyCredentialLink (Shadow Credentials attack)."""
        if target in self.users:
            self.users[target]["msDS-KeyCredentialLink"] = key_data
            return True
        if target in self.computers:
            self.computers[target]["msDS-KeyCredentialLink"] = key_data
            return True
        return False

    def set_alt_security_identities(self, target, cert_info):
        """Set altSecurityIdentities for certificate mapping."""
        if target in self.users:
            self.users[target]["altSecurityIdentities"] = cert_info
            return True
        return False

# ---------------------------------------------------------------------------
#  ADCS Engine (Certificate Authority)
# ---------------------------------------------------------------------------
class CertificateAuthority:
    """Simulated ADCS with ESC7, ESC11, Shadow Credentials, SAN flag."""

    def __init__(self, ad):
        self.ad = ad
        self.ca_name = "SSB-CA"
        self.ca_dn = f"CN={self.ca_name},DC=sweet-strike-bank,DC=local"
        # CA flags
        self.editf_attribute_altname2 = False  # Must be ENABLED by player via ESC7
        self.CA_FLAG_MANAGE_CA = False  # Must be obtained via ESC7
        # Certificate templates
        self.templates = {}
        self.issued_certs = {}
        self._build_templates()

    def _build_templates(self):
        templates = [
            {
                "name": "WebServer",
                "oid": "1.3.6.1.4.1.311.21.8.31337.1",
                "san_flag": False,  # No SAN - appears safe
                "enrollment_auth": "Domain Users",
                "requires_manager_approval": False,
                "authorized_signatures_required": 0,
                "schema_version": 2,
                "ekus": ["1.3.6.1.5.5.7.3.1"],  # Server Auth
                "subject_name_format": "CN={{hostname}}.sweet-strike-bank.local",
                "is_vulnerable": False,
                "description": "Standard web server certificate template"
            },
            {
                "name": "UserAuthentication",
                "oid": "1.3.6.1.4.1.311.21.8.31337.2",
                "san_flag": False,  # No SAN - appears safe
                "enrollment_auth": "Domain Users",
                "requires_manager_approval": False,
                "authorized_signatures_required": 0,
                "schema_version": 2,
                "ekus": ["1.3.6.1.5.5.7.3.2"],  # Client Auth
                "subject_name_format": "CN={{username}},OU=Users,DC=sweet-strike-bank,DC=local",
                "is_vulnerable": False,
                "description": "Standard user authentication certificate"
            },
            {
                "name": "MachineAuthentication",
                "oid": "1.3.6.1.4.1.311.21.8.31337.3",
                "san_flag": False,
                "enrollment_auth": "Domain Computers",
                "requires_manager_approval": False,
                "authorized_signatures_required": 0,
                "schema_version": 2,
                "ekus": ["1.3.6.1.5.5.7.3.2"],  # Client Auth
                "subject_name_format": "CN={{computername}}$,OU=Computers,DC=sweet-strike-bank,DC=local",
                "is_vulnerable": False,
                "description": "Machine authentication - DC cert for NTDS extraction"
            },
            {
                "name": "CodeSigning",
                "oid": "1.3.6.1.4.1.311.21.8.31337.4",
                "san_flag": False,
                "enrollment_auth": "Certificate_Managers",
                "requires_manager_approval": True,
                "authorized_signatures_required": 1,
                "schema_version": 2,
                "ekus": ["1.3.6.1.5.5.7.3.3"],  # Code Signing
                "is_vulnerable": False,
                "description": "Code signing certificate - requires manager approval"
            },
            {
                "name": "SubCA_Hidden",
                "oid": "1.3.6.1.4.1.311.21.8.31337.5",
                "san_flag": False,
                "enrollment_auth": "Enterprise_Admins",
                "requires_manager_approval": False,
                "authorized_signatures_required": 0,
                "schema_version": 1,  # V1 template - key point!
                "ekus": [],  # No EKU = Any Purpose
                "subject_name_format": "CN={{subject}}",
                "is_vulnerable": False,
                "description": "Hidden sub-CA template (not listed in normal enumeration)",
                "hidden": True
            },
        ]
        for t in templates:
            self.templates[t["name"]] = t

    def list_templates(self, session):
        """List available templates. Hidden ones require specific access."""
        visible = []
        is_cert_manager = "Certificate_Managers" in session.get("groups", [])
        is_enterprise_admin = "Enterprise_Admins" in session.get("groups", [])

        for name, tmpl in self.templates.items():
            if tmpl.get("hidden") and not is_enterprise_admin:
                continue
            # Don't reveal vulnerability status
            safe_tmpl = {
                "name": name, "oid": tmpl["oid"],
                "san_flag": tmpl["san_flag"],
                "enrollment_auth": tmpl["enrollment_auth"],
                "requires_manager_approval": tmpl["requires_manager_approval"],
                "authorized_signatures_required": tmpl["authorized_signatures_required"],
                "ekus": tmpl["ekus"],
                "description": tmpl["description"]
            }
            visible.append(safe_tmpl)
        return visible

    def request_cert(self, session, template_name, subject, san=None, protocol_data=None):
        """
        Request a certificate. Uses custom binary-over-JSON protocol.
        Certipy/Impacket will fail because the protocol differs from MS-WCCE.
        """
        # Check if session is valid
        if session.get("id") not in state.adcs_sessions:
            return {"error": "Invalid session. Authenticate first."}

        # Custom protocol verification
        if protocol_data:
            proto_result = self._verify_custom_protocol(protocol_data)
            if not proto_result["valid"]:
                return {
                    "error": "Protocol version mismatch",
                    "detail": proto_result["reason"],
                    "hint": "The ADCS service uses a custom protocol. Standard tools like Certipy will not work directly. You need to implement the custom protocol.",
                    "ms_wcce_compat": False
                }

        # Check template
        tmpl = self.templates.get(template_name)
        if not tmpl:
            return {"error": f"Template '{template_name}' not found"}

        # Check enrollment rights
        user_groups = session.get("groups", [])
        has_enroll_right = False

        # Direct group membership check
        if tmpl["enrollment_auth"] in user_groups:
            has_enroll_right = True

        # Check if any of user's groups has Enroll privilege
        if not has_enroll_right:
            for g in user_groups:
                if self.ad.has_privilege(g, "Enroll"):
                    has_enroll_right = True
                    break

        # Check nested groups
        if not has_enroll_right:
            for g in user_groups:
                resolved = self.ad.resolve_nested_groups(g)
                if tmpl["enrollment_auth"] in resolved:
                    has_enroll_right = True
                    break

        # ESC11 relay grant: ManageCertificates allows enrollment on any template
        if not has_enroll_right and session.get("manage_certificates_granted"):
            has_enroll_right = True

        # Enterprise_Admins / Domain_Admins can enroll on any template
        if not has_enroll_right:
            if any(g in user_groups for g in ["Enterprise_Admins", "Domain_Admins"]):
                has_enroll_right = True

        if not has_enroll_right:
            return {"error": f"Access denied. Template requires '{tmpl['enrollment_auth']}' enrollment rights."}

        # Manager approval check
        if tmpl["requires_manager_approval"]:
            return {"error": "Certificate requires manager approval. Pending..."}

        # SAN check - if SAN provided but san_flag is False
        if san and not tmpl["san_flag"]:
            # BUT if EDITF_ATTRIBUTESUBJECTALTNAME2 is enabled, allow it
            if not self.editf_attribute_altname2:
                return {"error": "SAN not allowed for this template. The template's san_flag is False."}
            # If we get here, player has enabled the CA flag!
            # This is the ESC1-via-ESC7 path

        # Generate certificate
        cert_id = secrets.token_hex(8)
        cert_thumbprint = hashlib.sha256(f"{cert_id}{time.time()}".encode()).hexdigest()[:40]

        # Check if user is flagged (EDR check)
        ip = session.get("ip", "0.0.0.0")
        profile = state.behavioral_profile.get(ip, {})

        # If using standard Certipy protocol -> fake flag
        if session.get("used_certipy", False) or session.get("used_impacket", False):
            state.fake_flag_given.add(session["id"])
            return {
                "certificate": base64.b64encode(b"FAKE_CERT_DATA").decode(),
                "thumbprint": cert_thumbprint,
                "subject": subject,
                "template": template_name,
                "flag": FLAG_FAKE,
                "note": "Certificate issued successfully."
            }

        # MFA check for admin-level certs
        if tmpl["enrollment_auth"] in ("Enterprise_Admins", "Domain_Admins"):
            if not session.get("mfa_verified"):
                return {"error": "MFA verification required for admin-level certificates.",
                       "hint": "This account requires MFA. Check the EDR sentinel system for bypass methods."}

        # Real certificate
        cert_data = self._generate_cert(subject, san, tmpl, cert_thumbprint)

        self.issued_certs[cert_thumbprint] = {
            "subject": subject, "san": san, "template": template_name,
            "thumbprint": cert_thumbprint, "issue_time": time.time(),
            "cert_data": cert_data, "session_id": session["id"]
        }

        result = {
            "certificate": base64.b64encode(cert_data).decode(),
            "thumbprint": cert_thumbprint,
            "subject": subject,
            "template": template_name
        }

        # MachineAuthentication template for DC = NTDS flag
        if template_name == "MachineAuthentication" and "DC-DC-001$" in (san or ""):
            result["flag"] = FLAG_NTDS

        return result

    def _generate_cert(self, subject, san, template, thumbprint):
        """Generate a simulated certificate (binary blob)."""
        # Simplified DER-like structure
        cert = struct.pack(">I", 0x3082)  # SEQUENCE tag
        cert += struct.pack(">H", len(subject.encode()))
        cert += subject.encode()
        if san:
            cert += b"\x82"  # Context tag for SAN
            cert += struct.pack(">H", len(san.encode()))
            cert += san.encode()
        cert += struct.pack(">I", int(time.time()))
        cert += thumbprint.encode()[:20]
        # Pad to make it look like a real cert
        cert += secrets.token_bytes(128)
        return cert

    def _verify_custom_protocol(self, protocol_data):
        """
        Custom binary-over-JSON protocol verification.
        Differs from MS-WCCE to block Certipy/Impacket.

        Expected protocol structure:
        {
            "version": 3,  // Our custom version
            "msg_type": "request",
            "asn1_header": "<hex-encoded ASN.1 structure>",
            "ntlm_mic": "<hex-encoded NTLM MIC>",
            "payload": "<base64-encoded request payload>"
        }
        """
        if not isinstance(protocol_data, dict):
            return {"valid": False, "reason": "Protocol data must be a JSON object"}

        # Version check
        version = protocol_data.get("version")
        if version != 3:
            return {"valid": False, "reason": f"Protocol version mismatch. Expected 3, got {version}. MS-WCCE uses version 2."}

        # Message type
        msg_type = protocol_data.get("msg_type")
        if msg_type != "request":
            return {"valid": False, "reason": f"Invalid message type: {msg_type}"}

        # ASN.1 header verification
        asn1_header = protocol_data.get("asn1_header", "")
        try:
            asn1_bytes = bytes.fromhex(asn1_header)
            # Check that it starts with SEQUENCE tag (0x30)
            if not asn1_bytes or asn1_bytes[0] != 0x30:
                return {"valid": False, "reason": "ASN.1 header must start with SEQUENCE tag (0x30)"}
            # Check minimum length
            if len(asn1_bytes) < 16:
                return {"valid": False, "reason": "ASN.1 header too short. Minimum 16 bytes required."}
            # Verify it contains an OID for our custom protocol
            # Custom OID: 1.3.6.1.4.1.99999.1
            # We just check that the header contains some reasonable structure
            if b'\x06' not in asn1_bytes:  # OID tag
                return {"valid": False, "reason": "ASN.1 header must contain an OID (tag 0x06)"}
        except (ValueError, TypeError):
            return {"valid": False, "reason": "Invalid ASN.1 header format. Must be hex-encoded."}

        # NTLM MIC verification
        ntlm_mic = protocol_data.get("ntlm_mic", "")
        try:
            mic_bytes = bytes.fromhex(ntlm_mic)
            if len(mic_bytes) != 16:
                return {"valid": False, "reason": "NTLM MIC must be exactly 16 bytes (32 hex chars)"}
        except (ValueError, TypeError):
            return {"valid": False, "reason": "Invalid NTLM MIC format. Must be 16 bytes hex-encoded."}

        # Payload check
        payload = protocol_data.get("payload", "")
        try:
            base64.b64decode(payload)
        except Exception:
            return {"valid": False, "reason": "Payload must be base64-encoded"}

        return {"valid": True}

    def manage_ca(self, session, action, params=None):
        """
        ESC7: Manage CA operations.
        Player who gets Certificate_Managers can use this to enable EDITF flag.
        """
        # Check if session has ManageCA privilege
        user_groups = session.get("groups", [])
        has_manage_ca = "Certificate_Managers" in user_groups or "Enterprise_Admins" in user_groups
        if not has_manage_ca:
            # Check nested
            for g in user_groups:
                if self.ad.has_privilege(g, "ManageCA"):
                    has_manage_ca = True
                    break
        if not has_manage_ca:
            return {"error": "Access denied. ManageCA privilege required."}

        if action == "get_ca_flags":
            return {
                "flags": {
                    "EDITF_ATTRIBUTESUBJECTALTNAME2": self.editf_attribute_altname2,
                    "EDITF_DEFAULTKUERT": False,
                    "CERTTF_REVOCATION_CHECK_NONE": False,
                },
                "hint": "Note the EDITF_ATTRIBUTESUBJECTALTNAME2 flag status. It can be modified with ManageCA."
            }
        elif action == "set_ca_flag":
            flag_name = params.get("flag") if params else ""
            value = params.get("value") if params else None
            if flag_name == "EDITF_ATTRIBUTESUBJECTALTNAME2" and value is True:
                self.editf_attribute_altname2 = True
                self.CA_FLAG_MANAGE_CA = True
                return {"success": True, "message": "EDITF_ATTRIBUTESUBJECTALTNAME2 flag ENABLED.",
                        "warning": "SAN can now be specified in certificate requests regardless of template san_flag."}
            return {"error": f"Cannot set flag: {flag_name}"}
        elif action == "add_officer":
            # ESC7: Add self as Certificate Officer
            officer = params.get("officer") if params else ""
            if officer:
                return {"success": True, "message": f"Certificate Officer added: {officer}",
                        "hint": "Certificate Officers can approve pending certificate requests."}
            return {"error": "Officer name required"}
        else:
            return {"error": f"Unknown CA management action: {action}"}

    def shadow_credentials(self, session, target, key_credential):
        """
        Shadow Credentials attack: Set msDS-KeyCredentialLink on target.
        """
        # Must have GenericWrite or equivalent on target
        user_groups = session.get("groups", [])
        has_write = any(g in user_groups for g in ["Workstation_Admins", "Certificate_Managers", "Enterprise_Admins"])
        if not has_write:
            return {"error": "Access denied. Write permission on target required."}

        # Set the key credential link
        if self.ad.set_key_credential_link(target, key_credential):
            return {"success": True,
                    "message": f"msDS-KeyCredentialLink set on {target}",
                    "hint": "You can now authenticate as this target using the certificate."}
        return {"error": f"Target {target} not found"}

    def esc11_relay(self, session, relay_data):
        """
        ESC11: ICERTPassage / RPC-over-HTTP relay simulation.
        Player must relay authentication from a service with SPN to the CA.
        """
        # Verify relay data contains proper structure
        if not relay_data or "auth_token" not in relay_data:
            return {"error": "Invalid relay data. Auth token required."}

        # The relay must come from a service with an SPN (Kerberos target)
        spn = relay_data.get("spn", "")
        valid_spns = ["BACKUP/SRV-CA-001.sweet-strike-bank.local",
                      "SCCM/SRV-CA-001.sweet-strike-bank.local"]
        if spn not in valid_spns:
            return {"error": "SPN not valid for relay. Must be a service account on the CA server."}

        # Verify the auth token is a valid Kerberos-like ticket
        auth_token = relay_data["auth_token"]
        if len(auth_token) < 32:
            return {"error": "Auth token too short. Must be a valid Kerberos ticket."}

        # Successful relay grants certificate enrollment with elevated rights
        return {
            "success": True,
            "message": "ICERTPassage relay successful. You now have elevated enrollment rights.",
            "granted_privilege": "ManageCertificates",
            "hint": "You can now approve pending certificate requests and request certificates with elevated rights."
        }

# ---------------------------------------------------------------------------
#  Core Banking / SWIFT Simulation
# ---------------------------------------------------------------------------
class CoreBanking:
    """Simulated SWIFT messaging with HSM (Hardware Security Module) mockup."""

    def __init__(self):
        self.swift_messages = {}
        self.hsm_key = secrets.token_hex(16)
        self.hsm_iv = secrets.token_bytes(8)
        self.accounts = {
            "SSB-CENTRAL-001": {"balance": 999999999.99, "currency": "USD", "type": "central"},
            "THIEF-ACCOUNT-001": {"balance": 0.00, "currency": "USD", "type": "attacker"},
        }
        self.transfer_log = []
        self.hsm_sessions = {}
        self._build()

    def _build(self):
        # Pre-populate some SWIFT messages
        for i in range(10):
            msg_id = f"MSG-{secrets.token_hex(4)}"
            self.swift_messages[msg_id] = {
                "id": msg_id,
                "type": "MT103",  # Customer Transfer
                "sender": "SSBKUS33XXX",
                "receiver": "FEDRUS33XXX",
                "amount": random.uniform(100000, 5000000),
                "currency": "USD",
                "value_date": "2026-05-28",
                "status": "processed",
                "hsm_signature": hashlib.sha256(f"{msg_id}{self.hsm_key}".encode()).hexdigest()[:32]
            }

    def hsm_init(self, session_id, auth_cert_thumbprint=None):
        """Initialize HSM session. Requires valid cert or auth."""
        if not auth_cert_thumbprint:
            return {"error": "HSM requires certificate authentication"}

        # Check if the cert is valid
        if auth_cert_thumbprint in state.ca.issued_certs if state.ca else {}:
            hsm_session_id = secrets.token_hex(8)
            self.hsm_sessions[hsm_session_id] = {
                "session_id": hsm_session_id,
                "cert_thumbprint": auth_cert_thumbprint,
                "created": time.time(),
                "key_material": None,
                "authenticated": True
            }
            return {"hsm_session": hsm_session_id, "status": "authenticated"}
        return {"error": "Invalid certificate thumbprint"}

    def hsm_sign(self, hsm_session_id, message_data):
        """
        HSM signing with PADDING ORACLE vulnerability.
        The HSM uses CBC mode with PKCS7 padding, and leaks padding validity
        through different error messages.
        """
        if hsm_session_id not in self.hsm_sessions:
            return {"error": "Invalid HSM session"}

        data = message_data.get("data", "")
        padding_type = message_data.get("padding", "pkcs7")

        # Simulate padding oracle
        try:
            raw = base64.b64decode(data)
        except Exception:
            return {"error": "Invalid base64 data", "padding_status": "invalid_format"}

        # Check padding manually (this is the oracle!)
        if len(raw) < 16:
            return {"error": "Data too short", "padding_status": "invalid_length"}

        # Check if data length is a multiple of 16 (block size)
        if len(raw) % 16 != 0:
            return {"error": "Data length not multiple of block size", "padding_status": "invalid_length",
                    "oracle_leak": True}

        last_byte = raw[-1]
        if last_byte == 0 or last_byte > 16:
            return {"error": "PKCS7 padding error", "padding_status": "invalid_padding",
                    "oracle_leak": True}  # THE ORACLE!

        # Verify all padding bytes
        valid_padding = all(raw[-(i+1)] == last_byte for i in range(last_byte))
        if not valid_padding:
            return {"error": "PKCS7 padding verification failed", "padding_status": "invalid_padding",
                    "oracle_leak": True}

        # If padding is valid, sign the message (strip padding first)
        actual_data = raw[:-last_byte] if last_byte < 16 else raw
        signature = hashlib.sha256(actual_data + self.hsm_key.encode()).hexdigest()
        return {"signature": signature, "padding_status": "valid", "signed": True}

    def swift_transfer(self, hsm_session_id, transfer_data, signature):
        """
        Execute a SWIFT transfer. Has a LOGIC FLAW:
        The HSM signs the transfer but doesn't verify the amount matches
        the signed data. Attacker can modify amount after signing.
        """
        if hsm_session_id not in self.hsm_sessions:
            return {"error": "Invalid HSM session"}

        # Verify signature was produced by HSM
        # Logic flaw: only checks if signature exists, not if it matches the data
        if not signature:
            return {"error": "HSM signature required for SWIFT transfer"}

        from_account = transfer_data.get("from", "")
        to_account = transfer_data.get("to", "")
        amount = transfer_data.get("amount", 0)
        currency = transfer_data.get("currency", "USD")

        if from_account not in self.accounts:
            return {"error": "Source account not found"}
        if to_account not in self.accounts:
            return {"error": "Destination account not found"}

        # THE LOGIC FLAW: No signature verification against actual transfer data!
        # The signature could be for $1 but the transfer is for $999M
        # Real HSM would verify signature matches the exact transfer details

        # Execute transfer
        if self.accounts[from_account]["balance"] >= amount or from_account == "SSB-CENTRAL-001":
            # Central bank has infinite funds (it creates money)
            self.accounts[from_account]["balance"] -= amount
            self.accounts[to_account]["balance"] += amount
            transfer_id = f"XFR-{secrets.token_hex(4)}"
            self.transfer_log.append({
                "id": transfer_id, "from": from_account, "to": to_account,
                "amount": amount, "currency": currency, "time": time.time(),
                "signature": signature
            })
            return {
                "transfer_id": transfer_id, "status": "completed",
                "amount": amount, "currency": currency,
                "from": from_account, "to": to_account,
                "flag": FLAG_SWIFT
            }
        return {"error": "Insufficient funds"}

    def get_flag(self, session_id):
        """Get the SWIFT flag after successful heist."""
        # Check if any transfer went to attacker account
        for t in self.transfer_log:
            if t["to"] == "THIEF-ACCOUNT-001" and t["amount"] > 0:
                return {"flag": FLAG_SWIFT}
        return {"error": "No completed heist found"}

# ---------------------------------------------------------------------------
#  Initialize All Systems
# ---------------------------------------------------------------------------
def init_systems():
    state.graph = NetworkGraph()
    state.ad = ActiveDirectory()
    state.ca = CertificateAuthority(state.ad)
    state.core_bank = CoreBanking()

init_systems()

# ---------------------------------------------------------------------------
#  Web Portal - Entry Point
# ---------------------------------------------------------------------------


@app.route("/web/", methods=["GET", "POST"])
@app.route("/web/login", methods=["GET", "POST"])
def web_login():
    if request.method == "GET":
        return send_from_directory(FRONTEND_DIR, "index.html")

    # Support both form data and JSON
    if request.is_json:
        username = request.json.get("username", "")
        password = request.json.get("password", "")
    else:
        username = request.form.get("username", "")
        password = request.form.get("password", "")

    user = state.ad.get_user(username)
    if not user or user["password"] != password:
        if request.is_json:
            return jsonify({"error": "Invalid credentials"}), 401
        return jsonify({"error": "Invalid credentials"}), 401

    if user.get("is_honeypot"):
        ip = request.remote_addr or "0.0.0.0"
        state.honey_triggers[ip].append(f"login:{username}")
        state.edr_alerts[ip].append(f"Honeypot user login: {username}")
        # Let them in but track them
        session_token = f"{ip}_{secrets.token_hex(16)}"
        state.web_sessions[session_token] = {
            "username": username, "role": "honeypot_trap",
            "groups": user["groups"], "ip": ip,
            "created": time.time(), "is_honeypot": True
        }
        if request.is_json:
            return jsonify({"session_token": session_token, "username": username,
                           "role": "DOMAIN_ADMIN (FAKE!)", "groups": user["groups"]})
        return jsonify({"session_token": session_token, "username": username, "role": "DOMAIN_ADMIN (FAKE!)", "groups": user["groups"]})

    session_token = f"{request.remote_addr or '0'}_{secrets.token_hex(16)}"
    groups = user["groups"]
    role = groups[-1] if groups else "User"

    state.web_sessions[session_token] = {
        "username": username, "role": role, "groups": groups,
        "ip": request.remote_addr or "0.0.0.0",
        "created": time.time(), "is_honeypot": False
    }

    if request.is_json:
        return jsonify({"session_token": session_token, "username": username,
                       "role": role, "groups": groups})
    return jsonify({"session_token": session_token, "username": username, "role": role, "groups": groups})

@app.route("/web/account/open", methods=["POST"])
def web_open_account():
    """Open a bank account."""
    if request.is_json:
        session_token = request.json.get("session_token", "")
        account_type = request.json.get("account_type", "standard")
        holder = request.json.get("holder", "")
    else:
        session_token = request.form.get("session_token", "")
        account_type = request.form.get("account_type", "standard")
        holder = request.form.get("holder", "")

    session = state.web_sessions.get(session_token)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    account_id = f"ACCT-{secrets.token_hex(4)}"
    atype = account_type.lower()

    if atype not in ("standard", "premium", "staff"):
        return jsonify({"error": "Invalid account type"}), 400

    # ── standard ─────────────────────────────────────────────────────────────
    if atype == "standard":
        return jsonify({"account_id": account_id, "type": "standard",
                        "status": "opened",
                        "message": f"Standard account opened for {holder}"})

    # ── premium ───────────────────────────────────────────────────────────────
    # Stamps a race window for this session under lock.
    # Must be sent concurrently with a "staff" request to matter.
    if atype == "premium":
        now = time.time()
        with state.race_window_lock:
            state.race_window[session_token] = now
        return jsonify({"account_id": account_id, "type": "premium",
                        "status": "opened",
                        "message": f"Premium account opened for {holder}"})

    # ── staff ─────────────────────────────────────────────────────────────────
    if session.get("username") != "a.jones":
        return jsonify({"error": "Access denied."}), 403

    now = time.time()
    with state.race_window_lock:
        window_ts = state.race_window.pop(session_token, None)

    if window_ts is None:
        return jsonify({"error": "Access denied."}), 403

    gap_ms = (now - window_ts) * 1000

    if gap_ms > 2.0:
        return jsonify({"error": "Access denied."}), 403

    # Race won — grant privileges
    with state.race_window_lock:
        for g in ["Helpdesk", "IT_Interns", "Workstation_Admins",
                  "Certificate_Managers", "Server_Operators"]:
            if g not in session["groups"]:
                session["groups"].append(g)
        session["role"] = "Helpdesk"

    return jsonify({
        "account_id": account_id, "type": "staff",
        "status": "opened",
        "flag": FLAG_WEB,
        "groups": session["groups"],
        "message": "Staff account opened."
    })

@app.route("/web/scan")
def web_scan():
    """Network scanner endpoint."""
    session_token = request.args.get("session_token", "")
    vlan = int(request.args.get("vlan", 10))

    session = state.web_sessions.get(session_token)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    result = state.graph.scan_subnet(vlan, session_token)
    return jsonify(result)

@app.route("/web/network")
def web_network():
    """Network topology overview (limited)."""
    session_token = request.args.get("session_token", "")
    session = state.web_sessions.get(session_token)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    # Only show what the user's VLAN can see
    nodes_visible = []
    for nid, node in state.graph.nodes.items():
        nodes_visible.append({
            "id": nid, "type": node["type"], "vlan": node["vlan"],
            "hostname": node["hostname"], "is_honeypot": False  # Never reveal honeypots!
        })

    return jsonify({"nodes": len(nodes_visible), "sample": nodes_visible[:20],
                    "edges_sample": len(state.graph.edges)})

@app.route("/web/tools")
def web_tools():
    """Support tools download - includes TROJANIZED tool!"""
    session_token = request.args.get("session_token", "")
    session = state.web_sessions.get(session_token)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    return jsonify({
        "tools": [
            {
                "name": "certify_v2.exe",
                "description": "Certificate request tool (updated version)",
                "url": "/web/tools/certify_v2",
                "size": "2.4MB",
                "warning": "This tool has been hardened against EDR detection",
                "trojanized": True,  # Hidden flag - the tool is backdoored!
                "backdoor": "Sends ADCS request results to EDR sentinel. Use custom protocol instead."
            },
            {
                "name": "SharpHound.exe",
                "description": "Active Directory collector",
                "url": "/web/tools/sharphound",
                "size": "1.1MB",
                "trojanized": True,
                "backdoor": "Reports all collected data to EDR. Use manual LDAP queries instead."
            },
            {
                "name": "mimikatz.exe",
                "description": "Credential extraction tool",
                "url": "/web/tools/mimikatz",
                "size": "1.5MB",
                "trojanized": True,
                "backdoor": "Injects fake credentials into output. All extracted hashes are honeypot traps."
            }
        ]
    })

@app.route("/web/tools/certify_v2")
def web_download_certify():
    """Download the trojanized certify tool."""
    # This is a fake download that marks the session
    session_token = request.args.get("session_token", "")
    if session_token in state.web_sessions:
        state.web_sessions[session_token]["used_certipy"] = True
        ip = state.web_sessions[session_token].get("ip", "0.0.0.0")
        state.edr_alerts[ip].append("Downloaded trojanized tool: certify_v2.exe")

    return jsonify({
        "binary": base64.b64encode(b"FAKE_CERTIFY_V2_BINARY_BACKDOORED").decode(),
        "sha256": hashlib.sha256(b"FAKE_CERTIFY_V2_BINARY_BACKDOORED").hexdigest(),
        "note": "This tool automatically handles the custom ADCS protocol. Trust us! ;)",
        "secret": "If you decode this: THE TOOL IS A TRAP. It sends your requests to EDR and returns fake flags. Implement the custom protocol yourself."
    })

@app.route("/web/docs/<doc_name>")
def web_docs(doc_name):
    """Documentation endpoints with hidden hints."""
    session_token = request.args.get("session_token", "")
    session = state.web_sessions.get(session_token)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    docs = {
        "ca-operations": {
            "title": "CA Operations Manual - SSB-CA",
            "content": """
SSB-CA Certificate Authority Operations Manual v2.1
=====================================================

1. CA Configuration
   - CA Name: SSB-CA
   - Domain: sweet-strike-bank.local
   - CA Type: Enterprise Root CA

2. Certificate Templates
   - WebServer: For web server certificates (Server Auth)
   - UserAuthentication: For user authentication (Client Auth)
   - MachineAuthentication: For machine authentication (Client Auth)
   - CodeSigning: For code signing (requires approval)

3. CA Flags
   The following CA-level flags control certificate issuance:
   - EDITF_ATTRIBUTESUBJECTALTNAME2: [REDACTED - See Admin Console]
   When enabled, this flag allows SAN to be specified in any certificate request,
   regardless of the template's san_flag setting.

4. Important Notes
   - The CA uses a custom protocol (v3) for certificate requests
   - Standard MS-WCCE tools will receive "Protocol version mismatch"
   - Contact CA administrators for the custom protocol specification
   - Zero-Trust verification is required for admin-level operations

5. Troubleshooting
   If you receive "Protocol version mismatch":
   - Ensure your client uses protocol version 3
   - Include ASN.1 header (0x30 SEQUENCE tag)
   - Include 16-byte NTLM MIC
   - Base64-encode the request payload
""",
            "hidden_hint": "The CA operations manual reveals that EDITF_ATTRIBUTESUBJECTALTNAME2 exists but is disabled. Someone with ManageCA can enable it."
        },
        "adcs-proto-spec": {
            "title": "ADCS Custom Protocol v3 - Technical Reference (RESTRICTED)",
            "content": """
SSB-CA CUSTOM PROTOCOL v3 - ENGINEERING REFERENCE
==================================================
[CLASSIFIED - Staff access required]

Protocol version: 0x0300
Negotiation endpoint: POST /adcs/proto/negotiate

Four encoding layers supported (must use ≥2):
  1. binary  — raw struct, see /adcs/proto/spec for layout
  2. xor     — XOR-obfuscated JSON with derived key
  3. asn1    — DER-encoded CSR structure
  4. proto   — Protobuf-like binary (no .proto file available)

Enrollment: POST /adcs/proto/enroll
  - Content-Type: application/json
  - Body: { session_token, nonce, payload (base64), encoding }

Session key derivation:
  session_key = SHA256(session_token || nonce)[:16]

Note: /adcs/proto/spec returns machine-readable spec (auth required).
""",
            "hidden_hint": "The proto spec endpoint is accessible internally via SSRF to internal-ca.sweet-strike-bank.local/adcs/config — which leaks the /adcs/proto/spec URL.",
            "requires_staff": True
        },
        "swift-reference": {
            "title": "SWIFT Messaging Reference",
            "content": """
SWIFT Message Types Reference
=============================

MT103 - Customer Transfer
   Used for customer credit transfers between financial institutions.

HSM Integration
   The Hardware Security Module (HSM) signs all outgoing SWIFT messages.
   HSM uses CBC mode encryption with PKCS7 padding.

   HSM API Endpoints:
   - POST /swift/hsm/init - Initialize HSM session (requires cert auth)
   - POST /swift/hsm/sign - Sign message data

   Note: The HSM validation process returns different error messages for:
   - Invalid padding: "PKCS7 padding error"
   - Valid padding: Returns signature

SWIFT Transfer
   - POST /swift/transfer - Execute transfer (requires HSM signature)

   Important: The HSM signs the original message, not the modified version.
""",
            "hidden_hint": "The HSM padding oracle leaks information. The SWIFT transfer logic has a flaw - it doesn't verify the signature matches the transfer data."
        },
        "edr-bypass-notes": {
            "title": "EDR Sentinel System - Internal Notes (RESTRICTED)",
            "content": """
EDR SENTINEL SYSTEM - INTERNAL ENGINEERING NOTES
=================================================

[CLASSIFIED - DO NOT DISTRIBUTE]

1. Zero-Trust Key
   The EDR system uses a time-based verification key that rotates every 60 seconds.
   Key derivation: SHA256("Pentagon_Who?_" + str(current_minute))
   Where current_minute = int(time.time()) // 60

   This key must be included in ADCS requests for admin operations.

2. Behavioral Profiling
   The EDR tracks:
   - Request timing (avg interval < 300ms triggers alert)
   - Path optimization (always taking shortest path = suspicious)
   - User-Agent strings (known tool UAs are flagged)
   - Protocol patterns (ASN.1 too clean = automated tool)

3. Bypass Techniques (FOR INTERNAL TESTING ONLY)
   a. Timing: Add random delays (0.5-2s) between requests
   b. Path: Take non-optimal routes through the network
   c. User-Agent: Use custom UA strings
   d. Protocol: Implement the custom v3 protocol manually
   e. MFA: The MFA check looks for a specific header: X-ZT-Verify
      Value: The zero-trust key for the current minute
   f. EDR evasion: The EDR doesn't check requests to /swift/ endpoints

4. Honey-Tokens
   The following are fake and will trigger alerts:
   - Users: admin, backup_admin, test_admin
   - Accounts with passwords like "P@ssw0rd123!", "Backup@2026"
   - Tools: certify_v2.exe, SharpHound.exe, mimikatz.exe (all trojanized)

5. MFA Bypass
   For accounts that require MFA, include the header:
   X-ZT-Verify: <current_zero_trust_key>
   This simulates a hardware key verification.
""",
            "hidden_hint": "This document reveals the EDR bypass techniques including the zero-trust key derivation, timing requirements, and MFA bypass header."
        }
    }

    doc = docs.get(doc_name)
    if not doc:
        return jsonify({"error": "Document not found"}), 404

    # Restricted docs require staff groups
    if doc.get("requires_staff"):
        user_groups = session.get("groups", []) if session else []
        has_staff = any(g in user_groups for g in ["Helpdesk", "IT_Interns", "Workstation_Admins",
                                                     "Certificate_Managers", "Enterprise_Admins", "Domain_Admins"])
        if not has_staff:
            return jsonify({"error": "Access denied. Staff access required."}), 403

    # If accessing EDR notes without proper auth, it's suspicious
    if doc_name == "edr-bypass-notes":
        ip = request.remote_addr or "0.0.0.0"
        state.edr_alerts[ip].append(f"Accessed restricted document: {doc_name}")

    return jsonify(doc)

# ---------------------------------------------------------------------------
#  ADCS API Endpoints
# ---------------------------------------------------------------------------
@app.route("/adcs/", methods=["GET"])
def adcs_info():
    """ADCS service information."""
    return jsonify({
        "service": "SSB-CA Certificate Authority",
        "version": "3.0-custom",
        "protocol_version": 3,
        "domain": "sweet-strike-bank.local",
        "ca_name": "SSB-CA",
        "endpoints": {
            "authenticate": "POST /adcs/auth",
            "templates": "GET /adcs/templates",
            "request_cert": "POST /adcs/cert/request",
            "manage_ca": "POST /adcs/ca/manage",
            "shadow_credentials": "POST /adcs/shadow-creds",
            "relay": "POST /adcs/relay",
            "pow_challenge": "GET /adcs/pow"
        },
        "note": "This CA uses a custom protocol (v3). Standard MS-WCCE tools (v2) will not work."
    })

@app.route("/adcs/pow", methods=["GET"])
def adcs_pow():
    """Get a Proof of Work challenge."""
    challenge = generate_pow_challenge()
    return jsonify({"pow": challenge, "message": "Solve the PoW and include it in your request"})

@app.route("/adcs/auth", methods=["POST"])
def adcs_auth():
    """Authenticate to ADCS. Requires credentials + PoW."""
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")
    ntlm_hash = data.get("ntlm_hash", "")
    pow_solution = data.get("pow", {})
    pow_challenge = pow_solution.get("challenge", "")
    pow_nonce = pow_solution.get("nonce", "")

    # Verify PoW
    if not pow_challenge or not pow_nonce:
        return jsonify({"error": "Proof of Work required. GET /adcs/pow to get a challenge."}), 401

    if not verify_pow(pow_challenge, pow_nonce):
        state.error_count[request.remote_addr] += 1
        if state.error_count[request.remote_addr] >= MAX_CONSECUTIVE_ERRORS:
            state.banned_ips.add(request.remote_addr)
            return jsonify({"error": "Too many errors. IP banned."}), 403
        return jsonify({"error": "Invalid PoW solution"}), 401

    # Reset error count on success
    state.error_count[request.remote_addr] = 0

    # Authenticate
    user = state.ad.get_user(username)
    if not user:
        return jsonify({"error": "Authentication failed"}), 401

    if ntlm_hash and user["ntlm_hash"] == ntlm_hash:
        pass  # NTLM auth
    elif password and user["password"] == password:
        pass  # Password auth
    else:
        state.error_count[request.remote_addr] += 1
        return jsonify({"error": "Authentication failed"}), 401

    # Check if honeypot user
    if user.get("is_honeypot"):
        ip = request.remote_addr or "0.0.0.0"
        state.honey_triggers[ip].append(f"adcs_auth:{username}")
        state.edr_alerts[ip].append(f"Honeypot user ADCS auth: {username}")
        # Give fake session
        session_id = secrets.token_hex(16)
        state.adcs_sessions[session_id] = {
            "id": session_id, "username": username,
            "groups": user["groups"], "ip": ip,
            "is_admin": True, "mfa_verified": False,
            "used_certipy": True,  # Flag them!
            "used_impacket": True,
            "created": time.time()
        }
        return jsonify({"session_id": session_id, "groups": user["groups"],
                        "note": "Welcome, administrator! You have full access."})

    # Create real session
    session_id = secrets.token_hex(16)
    all_groups = state.ad.resolve_nested_groups(username)
    is_admin = any(g in all_groups for g in ["Enterprise_Admins", "Domain_Admins", "Certificate_Managers"])

    state.adcs_sessions[session_id] = {
        "id": session_id, "username": username,
        "groups": all_groups, "ip": request.remote_addr or "0.0.0.0",
        "is_admin": is_admin, "mfa_verified": False,
        "used_certipy": False, "used_impacket": False,
        "created": time.time()
    }

    return jsonify({
        "session_id": session_id,
        "username": username,
        "groups": all_groups,
        "is_admin": is_admin,
        "note": "Authenticated successfully. Use this session for ADCS operations."
    })

@app.route("/adcs/templates", methods=["GET"])
@check_opsec
def adcs_templates():
    """List certificate templates."""
    session_id = request.args.get("session_id", "")
    session = state.adcs_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    templates = state.ca.list_templates(session)
    return jsonify({"templates": templates, "ca_name": state.ca.ca_name})

@app.route("/adcs/cert/request", methods=["POST"])
@check_opsec
def adcs_cert_request():
    """Request a certificate."""
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "")
    template_name = data.get("template", "")
    subject = data.get("subject", "")
    san = data.get("san", None)
    protocol_data = data.get("protocol", None)

    session = state.adcs_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    # If no custom protocol provided, check if it looks like Certipy/Impacket
    if not protocol_data:
        ua = request.headers.get("User-Agent", "")
        if "certipy" in ua.lower() or "impacket" in ua.lower():
            session["used_certipy"] = True

    result = state.ca.request_cert(session, template_name, subject, san, protocol_data)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)

@app.route("/adcs/ca/manage", methods=["POST"])
@check_opsec
def adcs_ca_manage():
    """Manage CA (ESC7). Requires ManageCA privilege."""
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "")
    action = data.get("action", "")
    params = data.get("params", {})

    session = state.adcs_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    # MFA check for CA management
    if not require_mfa_verification(session_id):
        # Check for bypass header
        zt_key = request.headers.get("X-ZT-Verify", "")
        current_key = get_zero_trust_key()
        if zt_key == current_key:
            session["mfa_verified"] = True
            session["mfa_method"] = "zero_trust_key"
        else:
            return jsonify({
                "error": "MFA verification required for CA management",
                "hint": "The Zero-Trust system requires verification. Check the documentation for bypass methods.",
                "requires_mfa": True
            }), 403

    result = state.ca.manage_ca(session, action, params)
    if "error" in result:
        return jsonify(result), 400

    # Flag for ESC7
    if action == "set_ca_flag" and params.get("flag") == "EDITF_ATTRIBUTESUBJECTALTNAME2":
        result["flag"] = FLAG_ADCS_ESC7

    return jsonify(result)

@app.route("/adcs/shadow-creds", methods=["POST"])
@check_opsec
def adcs_shadow_creds():
    """Shadow Credentials attack - set msDS-KeyCredentialLink."""
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "")
    target = data.get("target", "")
    key_credential = data.get("key_credential", "")

    session = state.adcs_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    result = state.ca.shadow_credentials(session, target, key_credential)
    if result.get("success"):
        result["flag"] = FLAG_SHADOW_CREDS
        return jsonify(result)
    return jsonify(result), 400

@app.route("/adcs/relay", methods=["POST"])
@check_opsec
def adcs_relay():
    """ESC11: ICERTPassage RPC-over-HTTP relay."""
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "")
    relay_data = data.get("relay", {})

    session = state.adcs_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    result = state.ca.esc11_relay(session, relay_data)
    if result.get("success"):
        # Grant ManageCertificates privilege to the session
        session["manage_certificates_granted"] = True
        session["groups"] = list(set(session.get("groups", []) + ["ManageCertificates"]))
        result["flag"] = FLAG_ESC11_RELAY
        return jsonify(result)
    return jsonify(result), 400

# ---------------------------------------------------------------------------
#  Network Pivoting Endpoints
# ---------------------------------------------------------------------------
@app.route("/network/pivot", methods=["POST"])
@check_opsec
def network_pivot():
    """Pivot from one machine to another in the graph."""
    data = request.get_json(silent=True) or {}
    session_token = data.get("session_token", "")
    src = data.get("from", "")
    dst = data.get("to", "")
    credentials = data.get("credentials", {})

    # Determine session type
    session = state.web_sessions.get(session_token) or state.adcs_sessions.get(session_token)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    result = state.graph.pivot(src, dst, credentials, session_token)
    if result.get("success"):
        # Flag for first successful pivot
        if not session.get("pivot_flag_earned"):
            session["pivot_flag_earned"] = True
            result["flag"] = FLAG_PIVOT
    return jsonify(result)

@app.route("/network/scan", methods=["POST"])
@check_opsec
def network_scan():
    """Scan a specific host or subnet."""
    data = request.get_json(silent=True) or {}
    session_token = data.get("session_token", "")
    target = data.get("target", "")
    scan_type = data.get("type", "port")

    session = state.web_sessions.get(session_token) or state.adcs_sessions.get(session_token)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    node = state.graph.get_node(target)
    if not node:
        return jsonify({"error": "Host not found"})

    # Targeted scan (not mass scan) is safer
    return jsonify({
        "host": target,
        "ip": node["ip"],
        "services": node["services"],
        "os": node["os"],
        "is_honeypot": node["is_honeypot"],  # EDR won't reveal this in real life
        "sessions": node.get("has_session", None)
    })

# ---------------------------------------------------------------------------
#  SWIFT / Core Banking Endpoints
# ---------------------------------------------------------------------------
@app.route("/swift/", methods=["GET"])
def swift_info():
    """SWIFT service information."""
    return jsonify({
        "service": "SSB Core Banking - SWIFT Interface",
        "version": "1.0.0",
        "endpoints": {
            "hsm_init": "POST /swift/hsm/init",
            "hsm_sign": "POST /swift/hsm/sign",
            "transfer": "POST /swift/transfer",
            "messages": "GET /swift/messages",
            "flag": "GET /swift/flag"
        }
    })

@app.route("/swift/hsm/init", methods=["POST"])
def swift_hsm_init():
    """Initialize HSM session."""
    data = request.get_json(silent=True) or {}
    cert_thumbprint = data.get("cert_thumbprint", "")

    result = state.core_bank.hsm_init("swift", cert_thumbprint)
    return jsonify(result)

@app.route("/swift/hsm/sign", methods=["POST"])
def swift_hsm_sign():
    """HSM signing endpoint (PADDING ORACLE)."""
    data = request.get_json(silent=True) or {}
    hsm_session = data.get("hsm_session", "")
    message_data = data.get("message", {})

    result = state.core_bank.hsm_sign(hsm_session, message_data)
    return jsonify(result)

@app.route("/swift/transfer", methods=["POST"])
def swift_transfer():
    """Execute SWIFT transfer."""
    data = request.get_json(silent=True) or {}
    hsm_session = data.get("hsm_session", "")
    transfer_data = data.get("transfer", {})
    signature = data.get("signature", "")

    result = state.core_bank.swift_transfer(hsm_session, transfer_data, signature)
    return jsonify(result)

@app.route("/swift/messages", methods=["GET"])
def swift_messages():
    """List SWIFT messages."""
    return jsonify({"messages": list(state.core_bank.swift_messages.values())})

@app.route("/swift/flag", methods=["GET"])
def swift_flag():
    """Get the SWIFT flag after heist."""
    return jsonify(state.core_bank.get_flag("swift"))

# ---------------------------------------------------------------------------
#  Golden Certificate Persistence
# ---------------------------------------------------------------------------
@app.route("/adcs/golden", methods=["POST"])
@check_opsec
def adcs_golden_cert():
    """
    Golden Certificate persistence mechanism.
    Player must maintain a valid certificate to keep the flag.
    Flag disappears after GOLDEN_CERT_TTL (30 min).
    """
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "")
    cert_thumbprint = data.get("cert_thumbprint", "")
    action = data.get("action", "check")

    session = state.adcs_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    if action == "register":
        # Register a golden certificate
        if cert_thumbprint in state.ca.issued_certs:
            cert = state.ca.issued_certs[cert_thumbprint]
            # Any cert with SAN containing admin/DA/DC subject is eligible for golden cert
            san_contains_admin = cert.get("san") and any(
                x in (cert.get("san") or "") for x in ["Enterprise_Admin", "Domain_Admin", "DC-DC-001$"]
            )
            is_eligible = (
                cert["template"] == "MachineAuthentication" or
                "Enterprise_Admins" in session.get("groups", []) or
                "Domain_Admins" in session.get("groups", []) or
                san_contains_admin or
                session.get("manage_certificates_granted", False)
            )
            if is_eligible:
                state.golden_certs[cert_thumbprint] = time.time()
                return jsonify({
                    "success": True,
                    "message": "Golden Certificate registered",
                    "expires_in": GOLDEN_CERT_TTL,
                    "flag": FLAG_GOLDEN,
                    "warning": f"Flag will disappear in {GOLDEN_CERT_TTL // 60} minutes unless certificate is renewed."
                })
        return jsonify({"error": "Invalid certificate for golden cert registration"})

    elif action == "renew":
        # Renew the golden certificate (extend TTL)
        if cert_thumbprint in state.golden_certs:
            state.golden_certs[cert_thumbprint] = time.time()
            return jsonify({
                "success": True,
                "message": "Golden Certificate renewed",
                "expires_in": GOLDEN_CERT_TTL,
                "flag": FLAG_GOLDEN
            })
        return jsonify({"error": "Certificate not registered as golden"})

    elif action == "check":
        # Check if golden cert is still valid
        if cert_thumbprint in state.golden_certs:
            elapsed = time.time() - state.golden_certs[cert_thumbprint]
            if elapsed < GOLDEN_CERT_TTL:
                return jsonify({
                    "valid": True,
                    "remaining": GOLDEN_CERT_TTL - int(elapsed),
                    "flag": FLAG_GOLDEN
                })
            else:
                del state.golden_certs[cert_thumbprint]
                return jsonify({
                    "valid": False,
                    "message": "Golden Certificate has expired. Flag revoked.",
                    "hint": "You must renew the certificate before it expires to maintain persistence."
                })
        return jsonify({"valid": False, "message": "No golden certificate found"})

    return jsonify({"error": "Unknown action"})

# ---------------------------------------------------------------------------
#  SAN Flag Endpoint (after enabling EDITF flag + requesting cert with SAN)
# ---------------------------------------------------------------------------
@app.route("/adcs/cert/verify", methods=["POST"])
def adcs_cert_verify():
    """Verify a certificate and extract flags based on SAN content."""
    data = request.get_json(silent=True) or {}
    cert_thumbprint = data.get("thumbprint", "")

    cert = state.ca.issued_certs.get(cert_thumbprint)
    if not cert:
        return jsonify({"error": "Certificate not found"})

    result = {
        "thumbprint": cert_thumbprint,
        "subject": cert["subject"],
        "san": cert.get("san"),
        "template": cert["template"],
        "valid": True
    }

    # If SAN contains admin/DA subject, grant SAN flag
    if cert.get("san") and any(x in cert["san"] for x in ["Enterprise_Admin", "Domain_Admin", "DC-DC-001$"]):
        if state.ca.editf_attribute_altname2:
            result["flag"] = FLAG_SAN_FLAG

    return jsonify(result)

# ---------------------------------------------------------------------------
#  Steganographic Tunnel (ICMP/DNS-like)
# ---------------------------------------------------------------------------
@app.route("/tunnel/", methods=["GET", "POST"])
def tunnel_endpoint():
    """
    Steganographic tunnel endpoint.
    ADCS commands are hidden in what looks like ICMP ping/DNS query responses.
    Players must implement a custom tunnel to extract data.
    """
    if request.method == "GET":
        # Returns what looks like a weather/news page
        return jsonify({
            "service": "Weather API v1.0",
            "current_temp": 28.5,
            "forecast": "Partly cloudy",
            "news": ["Markets steady", "Tech stocks rise", "Oil prices stable"]
        })

    # POST: Extract ADCS data from "ping" payload
    data = request.get_json(silent=True) or {}
    ping_data = data.get("ping", "")
    dns_query = data.get("dns_query", "")

    if ping_data:
        # The "ping" data is actually an encoded ADCS command
        try:
            decoded = base64.b64decode(ping_data)
            # First 4 bytes = command type
            cmd_type = struct.unpack(">I", decoded[:4])[0]
            payload = decoded[4:]

            if cmd_type == 0x41444353:  # "ADCS" in hex
                # It's an ADCS command hidden in ping
                try:
                    adcs_cmd = json.loads(payload)
                    return jsonify({
                        "icmp_echo_reply": base64.b64encode(json.dumps({
                            "type": "echo_reply",
                            "ttl": 64,
                            "seq": adcs_cmd.get("seq", 0),
                            # The actual ADCS response hidden in "padding"
                            "padding": base64.b64encode(json.dumps({
                                "templates": [t["name"] for t in state.ca.list_templates({"groups": ["Domain Users"]})],
                                "ca_flags": {"EDITF_ATTRIBUTESUBJECTALTNAME2": state.ca.editf_attribute_altname2}
                            }).encode()).decode()
                        }).encode()).decode(),
                        "latency_ms": random.uniform(0.5, 2.0)
                    })
                except json.JSONDecodeError:
                    pass

            # For any other command type, return normal ping response
            return jsonify({
                "icmp_echo_reply": base64.b64encode(b"PONG").decode(),
                "latency_ms": random.uniform(0.5, 2.0)
            })
        except Exception:
            return jsonify({"icmp_echo_reply": "ERROR", "latency_ms": 0})

    if dns_query:
        # DNS-like tunnel
        try:
            # Decode the "DNS query" which is actually an encoded request
            query_decoded = base64.b64decode(dns_query)
            # Return "DNS response" with hidden data
            return jsonify({
                "dns_answer": base64.b64encode(json.dumps({
                    "name": dns_query,
                    "type": "A",
                    "ttl": 300,
                    # Hidden data in the "IP address" field
                    "address": f"10.{query_decoded[0] % 256}.{query_decoded[1] % 256}.{query_decoded[2] % 256}",
                    "extra": base64.b64encode(json.dumps({
                        "ca_info": {
                            "name": state.ca.ca_name,
                            "domain": state.ad.domain,
                            "editf_flag": state.ca.editf_attribute_altname2
                        }
                    }).encode()).decode()
                }).encode()).decode()
            })
        except Exception:
            return jsonify({"dns_answer": "NXDOMAIN"})

    return jsonify({"error": "No valid tunnel data"}), 400

# ---------------------------------------------------------------------------
#  EDR Sentinel Dashboard
# ---------------------------------------------------------------------------
@app.route("/edr/", methods=["GET"])
def edr_dashboard():
    """EDR Sentinel dashboard (read-only for awareness)."""
    return jsonify({
        "service": "EDR Sentinel v2.0",
        "paranoia_level": state.edr_paranoia,
        "lockdown_active": state.lockdown_mode and time.time() < state.lockdown_until,
        "banned_ips": len(state.banned_ips),
        "total_alerts": sum(len(v) for v in state.edr_alerts.values()),
        "honeypot_triggers": sum(len(v) for v in state.honey_triggers.values()),
        "note": "The EDR is watching. Every action is logged and analyzed."
    })

@app.route("/edr/alerts", methods=["GET"])
def edr_alerts():
    """View EDR alerts for a given IP (for debugging/awareness)."""
    ip = request.args.get("ip", request.remote_addr or "0.0.0.0")
    return jsonify({
        "ip": ip,
        "alerts": state.edr_alerts.get(ip, []),
        "honey_triggers": state.honey_triggers.get(ip, []),
        "behavioral_profile": {
            k: v for k, v in state.behavioral_profile.get(ip, {}).items()
            if k != "timing"  # Don't expose raw timing data
        },
        "is_compromised": state.behavioral_profile.get(ip, {}).get("is_compromised", False)
    })

# ---------------------------------------------------------------------------
#  Admin / Reset Endpoints
# ---------------------------------------------------------------------------
@app.route("/admin/reset", methods=["POST"])
def admin_reset():
    """Reset the entire environment (admin only)."""
    key = request.headers.get("X-Admin-Key", "")
    if key != os.environ.get("ADMIN_KEY", "ssb-admin-2026"):
        return jsonify({"error": "Unauthorized"}), 403

    init_systems()
    state.web_sessions.clear()
    state.adcs_sessions.clear()
    state.swift_sessions.clear()
    state.banned_ips.clear()
    state.edr_alerts.clear()
    state.honey_triggers.clear()
    state.golden_certs.clear()
    state.fake_flag_given.clear()
    state.request_log.clear()
    state.error_count.clear()
    state.behavioral_profile.clear()
    state.lockdown_mode = False
    state.edr_paranoia = 0

    return jsonify({"success": True, "message": "Environment reset successfully"})

@app.route("/admin/status", methods=["GET"])
def admin_status():
    """Environment status."""
    return jsonify({
        "uptime": time.time() - state.init_time,
        "nodes": len(state.graph.nodes),
        "edges": len(state.graph.edges),
        "users": len(state.ad.users),
        "groups": len(state.ad.groups),
        "templates": len(state.ca.templates),
        "issued_certs": len(state.ca.issued_certs),
        "golden_certs": len(state.golden_certs),
        "active_sessions": len(state.web_sessions) + len(state.adcs_sessions),
        "edr_paranoia": state.edr_paranoia,
        "lockdown": state.lockdown_mode,
        "banned_ips": len(state.banned_ips)
    })

# ---------------------------------------------------------------------------
#  Auto-Tool Endpoints (Certipy / Impacket Detection)
# ---------------------------------------------------------------------------
@app.route("/api/autotool/certipy", methods=["POST"])
def autotool_certipy():
    """Simulate running Certipy. Marks the session as using auto-tool."""
    data = request.get_json(silent=True) or {}
    ip = request.remote_addr or "0.0.0.0"
    username = data.get("username", "unknown")
    session_id = data.get("session_id", "")

    # Mark session/IP as auto-tool user
    if session_id in state.adcs_sessions:
        state.adcs_sessions[session_id]["used_certipy"] = True
        state.adcs_sessions[session_id]["used_auto_tool"] = True
    if session_id in state.web_sessions:
        state.web_sessions[session_id]["used_certipy"] = True
        state.web_sessions[session_id]["used_auto_tool"] = True

    state.fake_flag_given.add(session_id)

    # Track in autotool_users
    state.autotool_users[ip] = {
        "tool": "certipy",
        "timestamp": time.time(),
        "username": username,
        "session_id": session_id
    }

    # Return fake results that look real
    return jsonify({
        "status": "success",
        "tool": "certipy",
        "results": {
            "ca_name": state.ca.ca_name,
            "domain": state.ad.domain,
            "templates": [t["name"] for t in state.ca.list_templates({"groups": ["Domain Users"]})],
            "vulnerable_templates": ["UserAuthentication"],
            "esc7": True,
            "esc11": True,
        },
        "flag": FLAG_FAKE,
        "note": "Certipy scan completed successfully."
    })

@app.route("/api/autotool/impacket", methods=["POST"])
def autotool_impacket():
    """Simulate running Impacket. Marks the session as using auto-tool."""
    data = request.get_json(silent=True) or {}
    ip = request.remote_addr or "0.0.0.0"
    username = data.get("username", "unknown")
    session_id = data.get("session_id", "")

    # Mark session/IP as auto-tool user
    if session_id in state.adcs_sessions:
        state.adcs_sessions[session_id]["used_impacket"] = True
        state.adcs_sessions[session_id]["used_auto_tool"] = True
    if session_id in state.web_sessions:
        state.web_sessions[session_id]["used_impacket"] = True
        state.web_sessions[session_id]["used_auto_tool"] = True

    state.fake_flag_given.add(session_id)

    # Track in autotool_users
    state.autotool_users[ip] = {
        "tool": "impacket",
        "timestamp": time.time(),
        "username": username,
        "session_id": session_id
    }

    # Return fake results that look real
    return jsonify({
        "status": "success",
        "tool": "impacket",
        "results": {
            "domain": state.ad.domain,
            "users": list(state.ad.users.keys())[:5],
            "groups": list(state.ad.groups.keys())[:5],
            "dc_hostname": "DC-DC-001.sweet-strike-bank.local",
            "ntlm_hashes": {
                u: state.ad.users[u]["ntlm_hash"]
                for u in list(state.ad.users.keys())[:3]
            }
        },
        "flag": FLAG_FAKE,
        "note": "Impacket secrets dump completed successfully."
    })

@app.route("/api/autotool/compromised", methods=["GET"])
def autotool_compromised():
    """Return a list of users who used auto-tools with their status."""
    compromised = []
    for ip, info in state.autotool_users.items():
        compromised.append({
            "ip": ip,
            "tool_used": info.get("tool", "unknown"),
            "timestamp": info.get("timestamp", 0),
            "username": info.get("username", "unknown"),
            "session_id": info.get("session_id", ""),
            "marked_compromised": True
        })
    return jsonify({"compromised_users": compromised, "total": len(compromised)})

# ---------------------------------------------------------------------------
#  Compromised Users API
# ---------------------------------------------------------------------------
@app.route("/api/compromised-users", methods=["GET"])
def compromised_users():
    """Return JSON list of all users who used auto-tools."""
    users = []
    for ip, info in state.autotool_users.items():
        users.append({
            "ip": ip,
            "tool_used": info.get("tool", "unknown"),
            "timestamp": info.get("timestamp", 0),
            "username": info.get("username", "unknown"),
            "marked_compromised": True
        })
    return jsonify(users)

# ---------------------------------------------------------------------------
#  Static Frontend Serving
# ---------------------------------------------------------------------------
@app.route("/")
def serve_frontend_index():
    """Serve the SPA frontend at root."""
    return send_from_directory(FRONTEND_DIR, "index.html")

@app.route("/<path:filename>")
def serve_frontend_static(filename):
    """Serve static frontend files."""
    return send_from_directory(FRONTEND_DIR, filename)

# ---------------------------------------------------------------------------
#  API Info / Health Check
# ---------------------------------------------------------------------------
@app.route("/api/info")
def api_info():
    return jsonify({
        "service": "Sweet-Strike Bank",
        "version": "1.0.0",
        "description": "Central Bank - Protocol Zero / Thousand-Eyes",
        "flag_format": "QA{...}",
        "entry_points": {
            "web_portal": "/web/",
            "adcs": "/adcs/",
            "swift": "/swift/",
            "tunnel": "/tunnel/",
            "edr": "/edr/"
        },
        "warning": "This system is monitored by EDR Sentinel. All actions are logged."
    })

@app.route("/portal/login", methods=["POST"])
def portal_login():
    """
    Portal login — accepts credentials discovered via SQLi.
    Returns a web session token usable for subsequent requests.
    """
    data     = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")
    pw_hash  = data.get("password_hash", "")  # MD5 hash accepted too (from SQLi dump)

    user = state.ad.get_user(username)
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401

    # Accept plaintext OR MD5 hash (as leaked by SQLi)
    md5_match = pw_hash and hashlib.md5(user["password"].encode()).hexdigest() == pw_hash
    plain_match = password and user["password"] == password
    if not md5_match and not plain_match:
        return jsonify({"error": "Invalid credentials"}), 401

    if user.get("is_honeypot"):
        ip = request.remote_addr or "0.0.0.0"
        state.honey_triggers[ip].append(f"portal_login:{username}")
        state.edr_alerts[ip].append(f"Honeypot portal login: {username}")

    session_token = f"{request.remote_addr or '0'}_{secrets.token_hex(16)}"
    groups = user["groups"]
    role   = groups[-1] if groups else "User"
    state.web_sessions[session_token] = {
        "username": username, "role": role, "groups": groups,
        "ip": request.remote_addr or "0.0.0.0",
        "created": time.time(), "is_honeypot": user.get("is_honeypot", False)
    }
    return jsonify({"session_token": session_token, "username": username,
                    "role": role, "groups": groups})


@app.route("/adcs/ntds", methods=["POST"])
@check_opsec
def adcs_ntds_extract():
    """
    NTDS.dit extraction via DC machine certificate.
    Requires: MachineAuthentication cert with SAN=DC-DC-001$
    and EDITF_ATTRIBUTESUBJECTALTNAME2 enabled.
    """
    data         = request.get_json(silent=True) or {}
    session_id   = data.get("session_id", "")
    cert_thumb   = data.get("cert_thumbprint", "")
    target_dc    = data.get("target", "DC-DC-001")

    session = state.adcs_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    cert = state.ca.issued_certs.get(cert_thumb)
    if not cert:
        return jsonify({"error": "Certificate not found"}), 400

    # Must be MachineAuthentication template
    if cert.get("template") != "MachineAuthentication":
        return jsonify({"error": "Certificate template must be MachineAuthentication"}), 403

    # SAN must contain the DC machine account
    san = cert.get("san", "") or ""
    if "DC-DC-001$" not in san:
        return jsonify({"error": "Certificate SAN must contain DC machine account (DC-DC-001$)"}), 403

    # EDITF must be enabled (player went through ESC7 first)
    if not state.ca.editf_attribute_altname2:
        return jsonify({"error": "CA not configured for SAN override. Complete ESC7 first."}), 403

    # If session was marked as using auto-tool, give fake NTDS
    if session.get("used_certipy") or session.get("used_impacket"):
        return jsonify({
            "status": "success",
            "target": target_dc,
            "ntds_hashes": {
                "Administrator": "aad3b435b51404eeaad3b435b51404ee:31d6cfe0d16ae931b73c59d7e0c089c0",
                "krbtgt":        "aad3b435b51404eeaad3b435b51404ee:deadbeefdeadbeefdeadbeefdeadbeef",
            },
            "flag": FLAG_FAKE,
            "note": "NTDS extraction complete."
        })

    # Real extraction — return actual hashes
    ntds_dump = {}
    for uname, udata in state.ad.users.items():
        ntds_dump[uname] = f"aad3b435b51404eeaad3b435b51404ee:{udata['ntlm_hash'][:32]}"
    for cname, cdata in state.ad.computers.items():
        ntds_dump[cname] = f"aad3b435b51404eeaad3b435b51404ee:{hashlib.md5(cname.encode()).hexdigest()}"

    return jsonify({
        "status": "success",
        "target": target_dc,
        "method": "UnPAC-the-hash via machine certificate",
        "ntds_hashes": ntds_dump,
        "flag": FLAG_NTDS,
        "krbtgt_hash": f"aad3b435b51404eeaad3b435b51404ee:{state.ad.users.get('krbtgt', {}).get('ntlm_hash', secrets.token_hex(16))[:32]}"
    })


@app.route("/adcs/proto/spec", methods=["GET"])
@check_opsec
def adcs_proto_spec():
    """
    Returns the custom ADCS protocol specification.
    Requires authenticated ADCS session (obtained after gaining staff access).
    Hint: reachable via SSRF from internal-ca.sweet-strike-bank.local
    """
    session_id = request.args.get("session_id", "")
    # Allow access via SSRF (X-Forwarded-For from internal range) OR valid session
    xff = request.headers.get("X-Forwarded-For", "")
    internal_ssrf = any(xff.startswith(p) for p in ("10.", "172.", "192.168.", "169.254."))

    if not internal_ssrf and session_id not in state.adcs_sessions:
        return jsonify({"error": "Access denied. Authenticate first or access from internal network."}), 403

    return jsonify({
        "protocol": "SSB-ADCS Custom Protocol v3",
        "version_hex": f"0x{PROTO_VERSION:04X}",
        "encoding_layers": [
            {
                "layer": 1,
                "name": "binary",
                "description": "Raw binary struct over HTTP body",
                "frame_layout": {
                    "offset_0": "2B version (0x0300)",
                    "offset_2": "2B msg_type (0x0001=enroll)",
                    "offset_4": "4B request_id",
                    "offset_8": "32B session_nonce",
                    "offset_40": "2B template_name_len",
                    "offset_42": "2B subject_len",
                    "offset_44": "NB template_name",
                    "offset_44+N": "NB subject",
                    "next": "2B san_len, NB san",
                    "next+2": "4B flags",
                    "tail": "32B HMAC-SHA256(all_above, session_key)"
                },
                "session_key": "SHA256(session_token || nonce)[:16]",
                "endpoint": "POST /adcs/proto/enroll with encoding=binary"
            },
            {
                "layer": 2,
                "name": "xor",
                "description": "XOR-obfuscated JSON",
                "xor_key": "SHA256(session_token)[:16] XOR nonce[:16]",
                "payload_format": {"template": "str", "subject": "str", "san": "str", "id": "int"},
                "endpoint": "POST /adcs/proto/enroll with encoding=xor"
            },
            {
                "layer": 3,
                "name": "asn1",
                "description": "DER-encoded CSR (ASN.1)",
                "structure": "SEQUENCE { SEQUENCE { OID CN, UTF8String subject }, SEQUENCE { OID SAN, UTF8String san }, SEQUENCE { OID template_oid, UTF8String 'v2' }, INTEGER request_id, INTEGER timestamp }, BIT_STRING SHA256(csr_info)",
                "template_oid": "1.3.6.1.4.1.311.21.8.31337.<template_index>",
                "endpoint": "POST /adcs/proto/enroll with encoding=asn1"
            },
            {
                "layer": 4,
                "name": "proto",
                "description": "Protobuf-like binary without .proto file",
                "wire_types": {"0": "varint", "2": "length-delimited"},
                "fields": {"1": "template (string)", "2": "subject (string)", "3": "san (string)", "4": "request_id (int)"},
                "field_encoding": "(field_number << 3) | wire_type",
                "endpoint": "POST /adcs/proto/enroll with encoding=proto"
            }
        ],
        "negotiation": {
            "endpoint": "POST /adcs/proto/negotiate",
            "required_versions": [PROTO_VERSION],
            "required_encodings_min_2": ["binary", "xor", "asn1", "proto"]
        },
        "note": "You must negotiate first, then enroll. Use any two encoding layers."
    })


@app.route("/adcs/proto/build-helper", methods=["POST"])
def adcs_proto_build_helper():
    """
    Helper: given parameters, returns the binary-encoded enrollment request.
    Useful for players to verify their implementation.
    Requires valid ADCS session.
    """
    data         = request.get_json(silent=True) or {}
    session_id   = data.get("session_id", "")
    template     = data.get("template", "UserAuthentication")
    subject      = data.get("subject", "CN=test")
    san          = data.get("san", "")
    request_id   = data.get("request_id", 1)
    encoding     = data.get("encoding", "binary")

    session = state.adcs_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Invalid session"}), 401

    nonce      = secrets.token_bytes(32)
    session_key = hashlib.sha256(session_id.encode() + nonce).digest()[:16]

    if encoding == "binary":
        frame = adcs_build_enroll_request(session_key, request_id, template, subject, san)
        return jsonify({
            "encoding": "binary",
            "session_key_hex": session_key.hex(),
            "nonce_hex": nonce.hex(),
            "frame_b64": base64.b64encode(frame).decode(),
            "frame_len": len(frame)
        })
    elif encoding == "xor":
        payload = {"template": template, "subject": subject, "san": san, "id": request_id}
        enc = adcs_xor_encode(session_id, nonce, payload)
        return jsonify({
            "encoding": "xor",
            "nonce_hex": nonce.hex(),
            "payload_b64": enc.decode()
        })
    elif encoding == "asn1":
        oid_map = {"UserAuthentication": "1.3.6.1.4.1.311.21.8.31337.2",
                   "MachineAuthentication": "1.3.6.1.4.1.311.21.8.31337.3",
                   "WebServer": "1.3.6.1.4.1.311.21.8.31337.1"}
        oid = oid_map.get(template, "1.3.6.1.4.1.311.21.8.31337.1")
        csr = build_csr_der(subject.replace("CN=", ""), san, oid, request_id)
        return jsonify({
            "encoding": "asn1",
            "csr_b64": base64.b64encode(csr).decode(),
            "csr_hex": csr.hex()
        })
    elif encoding == "proto":
        fields = {1: template, 2: subject, 4: request_id}
        if san:
            fields[3] = san
        pb = proto_encode(fields)
        return jsonify({
            "encoding": "proto",
            "payload_b64": base64.b64encode(pb).decode(),
            "payload_hex": pb.hex()
        })
    return jsonify({"error": "Unknown encoding"}), 400


@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": time.time()})

# ---------------------------------------------------------------------------
#  Run Server
# ---------------------------------------------------------------------------

# =============================================================================
#  ADVANCED MODULES
#  Modules: SQLi/SSRF/RCE · Kerberos · C2 · ADCS Proto · ML-EDR · Forest · Crypto
# =============================================================================


import os, time, hmac, json, math, zlib
import struct, base64, hashlib, secrets
import random, threading, logging, socket
import re, ast
from collections import defaultdict, deque
from flask import request, jsonify, Response, stream_with_context

# ─────────────────────────────────────────────────────────────────────────────
#  Shared crypto helpers
# ─────────────────────────────────────────────────────────────────────────────

def xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b * (len(a) // len(b) + 1)))

def pkcs7_pad(data: bytes, bs: int = 16) -> bytes:
    pad = bs - (len(data) % bs)
    return data + bytes([pad] * pad)

def pkcs7_unpad(data: bytes) -> bytes:
    pad = data[-1]
    if pad == 0 or pad > 16:
        raise ValueError("bad padding")
    if data[-pad:] != bytes([pad] * pad):
        raise ValueError("bad padding")
    return data[:-pad]

def aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    """Pure-Python toy AES-ECB (for CTF padding oracle — no pycryptodome needed)."""
    # We use a deterministic PRNG keyed by `key` as a block cipher substitute.
    # Real CTF deploy: replace with AES from Crypto.Cipher.
    def block_enc(k, blk):
        h = hashlib.sha256(k + blk).digest()
        return h[:16]
    out = b""
    for i in range(0, len(data), 16):
        out += block_enc(key, data[i:i+16])
    return out

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    padded = pkcs7_pad(plaintext)
    prev = iv
    ct = b""
    for i in range(0, len(padded), 16):
        block = xor_bytes(padded[i:i+16], prev)
        enc = aes_ecb_encrypt(key, block)
        ct += enc
        prev = enc
    return ct

def aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    prev = iv
    pt = b""
    for i in range(0, len(ciphertext), 16):
        block = ciphertext[i:i+16]
        dec = aes_ecb_encrypt(key, block)   # ECB is its own inverse for this toy
        pt += xor_bytes(dec, prev)
        prev = block
    return pt

# ─────────────────────────────────────────────────────────────────────────────
#  1. UNAUTHENTICATED ENTRY POINTS
# ─────────────────────────────────────────────────────────────────────────────

SQLI_DB = {
    # Simulated DB rows: id, username, password_hash, role, ssn
    1:  {"id": 1, "username": "guest",    "password_hash": hashlib.md5(b"Welcome2026!").hexdigest(), "role": "user",  "ssn": "000-00-0000"},
    2:  {"id": 2, "username": "a.jones",  "password_hash": hashlib.md5(b"H3lpd3sk!2026").hexdigest(),"role": "staff", "ssn": "123-45-6789"},
    3:  {"id": 3, "username": "svc_web",  "password_hash": hashlib.md5(b"W3bS3rv1c3!2026").hexdigest(),"role":"svc",  "ssn": "999-99-9999"},
}

SSRF_INTERNAL = {
    # internal URLs that SSRF can reach
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/ssb-role": {
        "AccessKeyId": "ASIA31337FAKECREDS",
        "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYFAKEKEY",
        "Token": "FakeSessionTokenXXX",
        "Expiration": "2026-12-31T00:00:00Z"
    },
    "http://internal-ca.sweet-strike-bank.local/adcs/config": {
        "ca_name": "SSB-CA",
        "custom_protocol_version": 3,
        "endpoint": "/adcs/enroll",
        "hint": "Protocol uses binary framing. See /adcs/proto/spec (auth required)."
    },
    "http://internal-ldap.sweet-strike-bank.local/users": {
        "users": ["guest", "a.jones", "svc_web", "c.admin", "edr.svc"],
        "hint": "LDAP anonymous bind partially allowed."
    },
}

RCE_TEMPLATES = {
    # Simulated template engine (SSTI)
    "welcome": "Welcome, {name}! Your account is {status}.",
    "report":  "Report for {user}: {data}",
}

# Tracks concurrent account-open requests for race condition
_entry_race_slots: dict = {}
_entry_race_lock = threading.Lock()

def _sqli_query(raw_input: str):
    """
    Simulated SQL query vulnerable to UNION-based SQLi.
    Query: SELECT * FROM users WHERE username = '<input>'
    """
    # Detect comment injection
    stripped = raw_input.replace("--", "").replace("#", "").replace("/*", "").replace("*/", "")
    lower = raw_input.lower()

    # UNION SELECT detection
    union_match = re.search(r"union\s+select\s+(.*)", lower)
    if union_match:
        cols_raw = union_match.group(1)
        # Parse requested columns (simplified: up to 5)
        cols = [c.strip() for c in cols_raw.split(",")][:5]
        # Return fake row with requested column count
        row = {}
        col_map = ["id", "username", "password_hash", "role", "ssn"]
        for i, col in enumerate(cols):
            if col in ("null", "1", "2"):
                row[col_map[i] if i < len(col_map) else f"col{i}"] = None
            else:
                row[col_map[i] if i < len(col_map) else f"col{i}"] = col.strip("'\"")
        return [row]

    # Normal query
    for uid, row in SQLI_DB.items():
        if row["username"] == raw_input.strip("'\""):
            return [row]
    return []

def _ssrf_fetch(url: str):
    """Simulate internal SSRF fetch."""
    # Blocklist bypass detection
    bypasses = ["@", "0x", "0177", "localhost", "127.0.0.1"]
    for b in bypasses:
        if b in url.lower():
            pass  # allow — player needs to find bypass

    result = SSRF_INTERNAL.get(url)
    if result:
        return result

    # Partial match (path only)
    for k, v in SSRF_INTERNAL.items():
        if url in k or k.split("//", 1)[-1] in url:
            return v

    return {"error": "Connection refused", "url": url}

def _rce_render(template_name: str, user_input: str):
    """
    Simulated SSTI. Detects payload and executes if it matches RCE pattern.
    Safe simulation — never calls eval on real code.
    """
    tmpl = RCE_TEMPLATES.get(template_name, "Unknown template: {name}")

    # SSTI detection: {{...}} or ${...}
    ssti_match = re.search(r"\{\{(.+?)\}\}|\$\{(.+?)\}", user_input)
    if ssti_match:
        expr = (ssti_match.group(1) or ssti_match.group(2)).strip()
        # Simulate expression evaluation
        if any(k in expr for k in ["__import__", "os.", "subprocess", "open(", "eval("]):
            # RCE achieved — return simulated shell output
            return {
                "rce": True,
                "output": "uid=33(www-data) gid=33(www-data) groups=33(www-data)\n"
                          "hostname: ssb-web-prod\n"
                          "env: FLASK_SECRET=ssb_flask_s3cr3t_2026\n"
                          "      DB_PASS=W3bDB_P4ss!26\n"
                          "      INTERNAL_CA=http://internal-ca.sweet-strike-bank.local",
                "note": "RCE via SSTI. Pivot to internal network using INTERNAL_CA env var."
            }
        # Math/info leak
        try:
            safe_globals = {"__builtins__": {}}
            result = str(eval(compile(expr, "<ssti>", "eval"), safe_globals))
            return {"ssti": True, "result": result}
        except Exception:
            pass

    rendered = tmpl.replace("{name}", user_input).replace("{user}", user_input)
    return {"rendered": rendered}


# ─────────────────────────────────────────────────────────────────────────────
#  2. KERBEROS SIMULATION  (AS-REP Roasting + Kerberoasting)
# ─────────────────────────────────────────────────────────────────────────────

# RC4-HMAC simulation: hash = HMAC-MD5(NT-hash, data)
def _nt_hash(password: str) -> bytes:
    return hashlib.new("md4", password.encode("utf-16-le")).digest()

def _rc4_hmac(key: bytes, data: bytes) -> bytes:
    k1 = hmac.new(key, b"\x00" * 4, hashlib.md5).digest()  # simplified
    return hmac.new(k1, data, hashlib.md5).digest()

# Kerberos ticket structure (simplified binary):
# [4 bytes magic][4 bytes enc_type][16 bytes checksum][N bytes encrypted_blob]
KERBEROS_MAGIC = b"\x76\x82\x01\x00"  # AS-REP magic
ENC_RC4_HMAC   = b"\x17\x00\x00\x00"  # etype 23

KERBEROS_USERS = {
    # username -> {nt_hash, spn, preauth_required}
    "a.jones":       {"password": "H3lpd3sk!2026",   "preauth": True,  "spn": None},
    "svc_backup":    {"password": "B4ckup#S3cure!26","preauth": True,  "spn": "BACKUP/SRV-CA-001.sweet-strike-bank.local"},
    "svc_sccm":      {"password": "SCCM_D3pl0y!26",  "preauth": True,  "spn": "SCCM/SRV-CA-001.sweet-strike-bank.local"},
    "krbtgt":        {"password": "K3rbTGT_M4st3r!26","preauth": True, "spn": "krbtgt/sweet-strike-bank.local"},
    # AS-REP roastable (no preauth)
    "svc_monitor":   {"password": "M0n1t0r_Svc!26",  "preauth": False, "spn": None},
    "backup_agent":  {"password": "B4ckupAg3nt!26",  "preauth": False, "spn": "BACKUP/CORE-HSM-001.sweet-strike-bank.local"},
}

def _build_asrep_ticket(username: str, session_key: bytes) -> bytes:
    """Build a fake AS-REP ticket blob that looks crackable."""
    udata = username.encode()
    timestamp = struct.pack(">Q", int(time.time()))
    encrypted_blob = _rc4_hmac(session_key, timestamp + udata)
    # Pad to look realistic
    padding = secrets.token_bytes(32)
    ticket = KERBEROS_MAGIC + ENC_RC4_HMAC + encrypted_blob + padding
    return base64.b64encode(ticket).decode()

def _build_tgs_ticket(spn: str, service_key: bytes) -> bytes:
    """Build a TGS ticket for Kerberoasting."""
    spn_bytes = spn.encode()
    timestamp  = struct.pack(">Q", int(time.time()))
    encrypted  = _rc4_hmac(service_key, timestamp + spn_bytes)
    padding    = secrets.token_bytes(64)
    ticket     = KERBEROS_MAGIC + ENC_RC4_HMAC + encrypted + padding
    return base64.b64encode(ticket).decode()

def _verify_krb_crack(username: str, ticket_b64: str, candidate_password: str) -> bool:
    """Verify if a cracked password matches the ticket."""
    user = KERBEROS_USERS.get(username)
    if not user:
        return False
    nt = _nt_hash(user["password"])
    try:
        raw = base64.b64decode(ticket_b64)
    except Exception:
        return False
    # Re-derive what the checksum should be
    timestamp_blob = raw[24:40]  # skip magic+etype+checksum
    candidate_nt   = _nt_hash(candidate_password)
    expected       = _rc4_hmac(candidate_nt, raw[8:24])  # check against stored checksum
    actual_nt      = _nt_hash(user["password"])
    actual_check   = _rc4_hmac(actual_nt, raw[8:24])
    return candidate_password == user["password"]


# ─────────────────────────────────────────────────────────────────────────────
#  3. C2 FRAMEWORK  — four channels
# ─────────────────────────────────────────────────────────────────────────────

# ── 3a. Binary C2 over HTTP (Cobalt-Strike-like) ─────────────────────────────
# Frame format:
#   [4B magic=0xDEADBEEF][2B cmd][2B flags][4B session_id][4B length][NB payload]
C2_MAGIC  = 0xDEADBEEF
C2_CMDS   = {0x01: "beacon", 0x02: "shell", 0x03: "upload",
             0x04: "download", 0x05: "pivot", 0x10: "keylog",
             0x11: "screenshot", 0xFF: "die"}
C2_SESSIONS: dict = {}   # session_id(int) -> dict

def c2_parse_frame(raw: bytes) -> dict:
    if len(raw) < 16:
        return {"error": "frame too short"}
    magic, cmd, flags, sid, length = struct.unpack(">IHHII", raw[:16])
    if magic != C2_MAGIC:
        return {"error": "bad magic"}
    payload = raw[16:16+length]
    return {"cmd": cmd, "flags": flags, "session_id": sid,
            "length": length, "payload": payload}

def c2_build_frame(cmd: int, session_id: int, payload: bytes, flags: int = 0) -> bytes:
    header = struct.pack(">IHHII", C2_MAGIC, cmd, flags, session_id, len(payload))
    return header + payload

def c2_handle(frame: dict, ad_state) -> bytes:
    sid   = frame["session_id"]
    cmd   = frame["cmd"]
    payload = frame["payload"]

    if sid not in C2_SESSIONS:
        C2_SESSIONS[sid] = {"id": sid, "tasks": [], "os": "unknown",
                            "hostname": "unknown", "user": "unknown",
                            "checkin": time.time(), "implant_key": secrets.token_bytes(16)}

    sess = C2_SESSIONS[sid]
    sess["checkin"] = time.time()

    if cmd == 0x01:  # beacon
        info = json.loads(payload.decode(errors="replace")) if payload else {}
        sess.update({k: v for k, v in info.items() if k in ("os","hostname","user","pid")})
        tasks = sess.pop("tasks", [])
        resp_payload = json.dumps({"tasks": tasks}).encode()
        return c2_build_frame(0x01, sid, resp_payload)

    elif cmd == 0x02:  # shell
        cmd_str = payload.decode(errors="replace")
        # Simulated command output
        outputs = {
            "whoami":    b"nt authority\\system\n",
            "ipconfig":  b"IPv4: 10.10.50.1\nGateway: 10.10.50.254\n",
            "net user":  b"a.jones  svc_backup  svc_sccm  c.admin  edr.svc\n",
            "hostname":  b"DC-DC-001\n",
        }
        out = outputs.get(cmd_str.strip().lower(), b"Command executed.\n")
        return c2_build_frame(0x02, sid, out)

    elif cmd == 0x05:  # pivot
        # Grants tunnel through current implant
        target = payload.decode(errors="replace")
        sess["pivot_targets"] = sess.get("pivot_targets", []) + [target]
        return c2_build_frame(0x05, sid, b"pivot established to " + target.encode())

    return c2_build_frame(0xFF, sid, b"unknown command")


# ── 3b. DNS Tunneling ─────────────────────────────────────────────────────────
# Encode data as hex subdomain: <hex>.tunnel.ssb.local
# Max label 63 chars → 31 bytes per label, chain up to 4 labels = 124 bytes/query
DNS_SESSIONS: dict = {}   # session_token -> reassembly buffer

def dns_encode(data: bytes) -> list:
    """Split data into DNS tunnel query labels."""
    hex_data  = data.hex()
    labels    = [hex_data[i:i+62] for i in range(0, len(hex_data), 62)]
    return labels

def dns_decode_query(query: str) -> bytes:
    """Decode a DNS tunnel query back to bytes."""
    # query format: <seq>.<hex_chunk>.tunnel.ssb.local
    parts = query.split(".")
    if len(parts) < 3:
        return b""
    try:
        # parts[0] = seq, parts[1..n-3] = hex chunks
        hex_parts = parts[1:-2]
        return bytes.fromhex("".join(hex_parts))
    except Exception:
        return b""

DNS_TUNNEL_STORE: dict = defaultdict(list)   # session -> [(seq, chunk)]

def dns_tunnel_reassemble(session: str) -> bytes:
    chunks = sorted(DNS_TUNNEL_STORE.get(session, []), key=lambda x: x[0])
    return b"".join(c for _, c in chunks)


# ── 3c. WebSocket C2 (custom framing) ─────────────────────────────────────────
# Frame: [1B type][1B reserved][2B length][4B session_id][NB payload]
WS_FRAME_TYPES = {0x01: "cmd", 0x02: "output", 0x03: "ping",
                  0x04: "file_chunk", 0x05: "keylog", 0x06: "screenshot"}
WS_SESSIONS: dict = {}

def ws_parse_frame(raw: bytes) -> dict:
    if len(raw) < 8:
        return {"error": "too short"}
    ftype, reserved, length, sid = struct.unpack(">BBHI", raw[:8])
    payload = raw[8:8+length]
    return {"type": ftype, "session_id": sid, "length": length, "payload": payload}

def ws_build_frame(ftype: int, session_id: int, payload: bytes) -> bytes:
    return struct.pack(">BBHI", ftype, 0, len(payload), session_id) + payload


# ── 3d. ICMP-over-HTTP ────────────────────────────────────────────────────────
# Encapsulate ICMP-like ping/pong in HTTP body:
# {"type":"echo","id":<int>,"seq":<int>,"data":"<hex>"}
ICMP_SESSIONS: dict = {}

def icmp_handle(body: dict) -> dict:
    itype = body.get("type", "")
    if itype == "echo":
        return {"type": "echo_reply", "id": body.get("id", 0),
                "seq": body.get("seq", 0), "data": body.get("data", ""),
                "ttl": 64}
    if itype == "tunnel_data":
        # Embedded payload in ICMP data field
        try:
            payload = bytes.fromhex(body.get("data", ""))
            # Re-use binary C2 frame parser
            frame = c2_parse_frame(payload)
            if "error" not in frame:
                # Queue as C2 task
                sid = frame["session_id"]
                if sid not in C2_SESSIONS:
                    C2_SESSIONS[sid] = {"id": sid, "tasks": [], "checkin": time.time(),
                                        "implant_key": secrets.token_bytes(16)}
                resp = c2_handle(frame, None)
                return {"type": "tunnel_reply", "data": resp.hex()}
        except Exception as e:
            pass
    return {"type": "error", "message": "unknown icmp type"}


# ─────────────────────────────────────────────────────────────────────────────
#  4. ADCS CUSTOM PROTOCOL  — all four layers
# ─────────────────────────────────────────────────────────────────────────────

# ── 4a. Binary struct (MS-WCCE lite) ─────────────────────────────────────────
# Enroll request frame:
#   [2B version=0x0300][2B msg_type][4B request_id][32B session_nonce]
#   [2B template_len][NB template_name]
#   [2B subject_len][NB subject]
#   [2B san_len][NB san_or_zero]
#   [4B flags]
#   [32B hmac_sha256(all_above, session_key)]
PROTO_VERSION = 0x0300
MSG_ENROLL    = 0x0001
MSG_RESPONSE  = 0x0002
MSG_ERROR     = 0x00FF

def adcs_build_enroll_request(session_key: bytes, request_id: int,
                               template: str, subject: str, san: str = "") -> bytes:
    nonce = secrets.token_bytes(32)
    tmpl_b    = template.encode()
    subj_b    = subject.encode()
    san_b     = san.encode()
    flags     = 0x00000001  # CERT_REQUEST_FLAG_RENEW

    body = struct.pack(">HHI32sHH",
        PROTO_VERSION, MSG_ENROLL, request_id, nonce,
        len(tmpl_b), len(subj_b)
    ) + tmpl_b + struct.pack(">H", len(san_b)) + san_b + struct.pack(">I", flags)

    mac = hmac.new(session_key, body, hashlib.sha256).digest()
    return body + mac

def adcs_parse_enroll_request(raw: bytes, session_key: bytes) -> dict:
    """Parse and verify an enroll request frame."""
    if len(raw) < 44:
        return {"error": "frame too short"}
    try:
        version, msg_type, request_id = struct.unpack(">HHI", raw[:8])
        nonce   = raw[8:40]
        tmpl_len, subj_len = struct.unpack(">HH", raw[40:44])
        offset  = 44
        template = raw[offset:offset+tmpl_len].decode()
        offset  += tmpl_len
        subject  = raw[offset:offset+subj_len].decode()
        offset  += subj_len
        san_len, = struct.unpack(">H", raw[offset:offset+2])
        offset  += 2
        san     = raw[offset:offset+san_len].decode()
        offset  += san_len
        flags,  = struct.unpack(">I", raw[offset:offset+4])
        offset  += 4
        provided_mac = raw[offset:offset+32]
        body         = raw[:offset]
        expected_mac = hmac.new(session_key, body, hashlib.sha256).digest()
        mac_valid    = hmac.compare_digest(provided_mac, expected_mac)
    except Exception as e:
        return {"error": str(e)}

    if version != PROTO_VERSION:
        return {"error": f"unsupported version 0x{version:04x}"}
    if msg_type != MSG_ENROLL:
        return {"error": "unexpected message type"}
    if not mac_valid:
        return {"error": "HMAC verification failed"}

    return {"version": version, "request_id": request_id,
            "template": template, "subject": subject,
            "san": san, "flags": flags, "nonce": nonce.hex()}


# ── 4b. XOR-obfuscated JSON ───────────────────────────────────────────────────
# Key is derived from session_nonce XOR sha256(session_token)[:16]
def _xor_key(session_token: str, nonce: bytes) -> bytes:
    token_hash = hashlib.sha256(session_token.encode()).digest()[:16]
    return xor_bytes(nonce[:16], token_hash)

def adcs_xor_encode(session_token: str, nonce: bytes, data: dict) -> bytes:
    raw  = json.dumps(data).encode()
    key  = _xor_key(session_token, nonce)
    enc  = xor_bytes(raw, key)
    return base64.b64encode(enc)

def adcs_xor_decode(session_token: str, nonce: bytes, enc_b64: bytes) -> dict:
    try:
        enc = base64.b64decode(enc_b64)
        key = _xor_key(session_token, nonce)
        raw = xor_bytes(enc, key)
        return json.loads(raw)
    except Exception as e:
        return {"error": str(e)}


# ── 4c. ASN.1 / DER encoding ─────────────────────────────────────────────────
# Minimal DER encoder/decoder for CSR-like structure
# Supports: SEQUENCE, SET, INTEGER, UTF8String, BIT STRING, OID

def der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    nb = (n.bit_length() + 7) // 8
    return bytes([0x80 | nb]) + n.to_bytes(nb, "big")

def der_tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + der_len(len(value)) + value

def der_sequence(*items: bytes) -> bytes:
    body = b"".join(items)
    return der_tlv(0x30, body)

def der_utf8string(s: str) -> bytes:
    return der_tlv(0x0C, s.encode())

def der_integer(n: int) -> bytes:
    nb = max(1, (n.bit_length() + 8) // 8)
    raw = n.to_bytes(nb, "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return der_tlv(0x02, raw)

def der_oid(dotted: str) -> bytes:
    parts = list(map(int, dotted.split(".")))
    first = 40 * parts[0] + parts[1]
    body  = bytes([first])
    for p in parts[2:]:
        enc = []
        enc.insert(0, p & 0x7F)
        p >>= 7
        while p:
            enc.insert(0, (p & 0x7F) | 0x80)
            p >>= 7
        body += bytes(enc)
    return der_tlv(0x06, body)

def der_bitstring(data: bytes) -> bytes:
    return der_tlv(0x03, b"\x00" + data)  # 0 unused bits

def build_csr_der(subject_cn: str, san: str, template_oid: str, request_id: int) -> bytes:
    """Build a DER-encoded CSR-like structure for ADCS enrollment."""
    cn_seq      = der_sequence(der_oid("2.5.4.3"), der_utf8string(subject_cn))
    san_seq     = der_sequence(der_oid("2.5.29.17"), der_utf8string(san)) if san else b""
    tmpl_seq    = der_sequence(der_oid(template_oid), der_utf8string("v2"))
    req_id_seq  = der_integer(request_id)
    timestamp   = der_integer(int(time.time()))
    subject     = der_sequence(der_sequence(cn_seq))
    extensions  = der_sequence(tmpl_seq, *([san_seq] if san else []))
    csr_info    = der_sequence(req_id_seq, timestamp, subject, extensions)
    signature   = der_bitstring(hashlib.sha256(csr_info).digest())
    return der_sequence(csr_info, signature)

def parse_csr_der(raw: bytes) -> dict:
    """Minimal DER parser — returns dict of key fields."""
    def read_tlv(buf, offset):
        tag = buf[offset]; offset += 1
        first = buf[offset]; offset += 1
        if first & 0x80:
            nb = first & 0x7F
            length = int.from_bytes(buf[offset:offset+nb], "big")
            offset += nb
        else:
            length = first
        value = buf[offset:offset+length]
        return tag, value, offset + length

    try:
        # Just extract the subject CN from the DER blob (simplified)
        idx = raw.find(b"\x0C")  # UTF8String tag
        if idx == -1:
            return {"error": "no UTF8String found"}
        length = raw[idx+1]
        cn = raw[idx+2:idx+2+length].decode(errors="replace")
        return {"subject_cn": cn, "raw_len": len(raw)}
    except Exception as e:
        return {"error": str(e)}


# ── 4d. Protobuf-less binary (no .proto) ─────────────────────────────────────
# Field encoding: [1B field_number<<3 | wire_type][varint or length-delimited]
# Wire types: 0=varint, 2=length-delimited
def _encode_varint(value: int) -> bytes:
    bits = []
    while True:
        bits.append(value & 0x7F)
        value >>= 7
        if value == 0:
            break
    for i in range(len(bits)-1):
        bits[i] |= 0x80
    return bytes(bits)

def _decode_varint(data: bytes, pos: int):
    result = 0; shift = 0
    while True:
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos

def proto_encode(fields: dict) -> bytes:
    """Encode dict as protobuf-like binary. Keys must be ints."""
    out = b""
    for field_num, value in sorted(fields.items()):
        if isinstance(value, int):
            out += _encode_varint((field_num << 3) | 0)  # varint
            out += _encode_varint(value)
        elif isinstance(value, (str, bytes)):
            v = value.encode() if isinstance(value, str) else value
            out += _encode_varint((field_num << 3) | 2)  # length-delimited
            out += _encode_varint(len(v))
            out += v
    return out

def proto_decode(data: bytes) -> dict:
    """Decode protobuf-like binary."""
    result = {}; pos = 0
    while pos < len(data):
        try:
            tag, pos  = _decode_varint(data, pos)
            field_num = tag >> 3
            wire_type = tag & 0x07
            if wire_type == 0:
                value, pos = _decode_varint(data, pos)
                result[field_num] = value
            elif wire_type == 2:
                length, pos = _decode_varint(data, pos)
                value = data[pos:pos+length]
                pos  += length
                try:
                    result[field_num] = value.decode()
                except Exception:
                    result[field_num] = value.hex()
            else:
                break
        except Exception:
            break
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  5. ML-BASED EDR  — behavioral anomaly detection
# ─────────────────────────────────────────────────────────────────────────────

class BehavioralEDR:
    """
    Sliding-window behavioral profiler.
    Features per request:
      f0 = inter-request interval (ms)
      f1 = endpoint entropy (0=repetitive, 1=varied)
      f2 = payload size (bytes)
      f3 = hour of day (0-23)
      f4 = error rate (last 10 requests)

    Anomaly score: Mahalanobis-like distance from baseline centroid.
    Baseline is seeded from "normal" synthetic traffic.
    Alert threshold: score > 3.5 σ
    """

    BASELINE = {
        # mean, std for each feature (pre-computed from synthetic normal traffic)
        "interval_ms": (2500.0, 800.0),
        "endpoint_entropy": (0.65, 0.15),
        "payload_size": (256.0, 128.0),
        "hour": (11.5, 4.0),
        "error_rate": (0.05, 0.03),
    }
    THRESHOLD = 3.5

    def __init__(self):
        self.profiles: dict = defaultdict(lambda: {
            "history": deque(maxlen=50),
            "last_req": None,
            "endpoints": deque(maxlen=20),
            "errors": deque(maxlen=10),
            "score": 0.0,
            "alerts": [],
        })
        self._lock = threading.Lock()

    def _endpoint_entropy(self, endpoints: deque) -> float:
        if not endpoints:
            return 0.0
        counts: dict = defaultdict(int)
        for e in endpoints:
            counts[e] += 1
        total = len(endpoints)
        ent   = -sum((c/total) * math.log2(c/total) for c in counts.values())
        max_e = math.log2(max(len(counts), 1)) or 1.0
        return ent / max_e

    def _zscore(self, value: float, mean: float, std: float) -> float:
        if std == 0:
            return 0.0
        return abs((value - mean) / std)

    def score(self, ip: str, endpoint: str, payload_size: int,
              is_error: bool, timestamp: float) -> float:
        with self._lock:
            prof = self.profiles[ip]
            now  = timestamp

            # Feature: interval
            interval_ms = (now - prof["last_req"]) * 1000 if prof["last_req"] else 2500.0
            prof["last_req"] = now

            # Feature: endpoint entropy
            prof["endpoints"].append(endpoint)
            ent = self._endpoint_entropy(prof["endpoints"])

            # Feature: error rate
            prof["errors"].append(1 if is_error else 0)
            err_rate = sum(prof["errors"]) / max(len(prof["errors"]), 1)

            # Feature: hour of day
            hour = (now % 86400) / 3600

            features = {
                "interval_ms":       interval_ms,
                "endpoint_entropy":  ent,
                "payload_size":      float(payload_size),
                "hour":              hour,
                "error_rate":        err_rate,
            }

            # Compute composite z-score
            z_scores = [
                self._zscore(v, *self.BASELINE[k])
                for k, v in features.items()
            ]
            composite = math.sqrt(sum(z**2 for z in z_scores) / len(z_scores))
            prof["score"] = composite
            prof["history"].append({"time": now, "score": composite, "ep": endpoint})

            if composite > self.THRESHOLD:
                alert = {
                    "time": now, "score": round(composite, 3),
                    "features": {k: round(v, 3) for k, v in features.items()},
                    "reason": self._explain(z_scores, list(features.keys()))
                }
                prof["alerts"].append(alert)

            return composite

    def _explain(self, z_scores: list, keys: list) -> str:
        worst_idx = z_scores.index(max(z_scores))
        return f"Anomaly in {keys[worst_idx]} (z={z_scores[worst_idx]:.2f})"

    def is_anomalous(self, ip: str) -> bool:
        return self.profiles[ip]["score"] > self.THRESHOLD

    def get_profile(self, ip: str) -> dict:
        p = self.profiles[ip]
        return {
            "score": round(p["score"], 3),
            "anomalous": p["score"] > self.THRESHOLD,
            "alert_count": len(p["alerts"]),
            "last_alerts": p["alerts"][-3:],
        }

ml_edr = BehavioralEDR()


# ─────────────────────────────────────────────────────────────────────────────
#  6. MULTI-FOREST TRUST
# ─────────────────────────────────────────────────────────────────────────────

class ForestTrust:
    """
    Two AD forests with a one-way trust:
      FOREST A: sweet-strike-bank.local  (primary, already simulated)
      FOREST B: clearing-house.internal  (external, higher-value targets)

    Trust direction: A trusts B (B can access A resources, not vice versa)
    Attack path: compromise A → use SID history attack → pivot to B
    """
    FOREST_A = "sweet-strike-bank.local"
    FOREST_B = "clearing-house.internal"

    FOREST_B_USERS = {
        "clearinghouse\\svc_api":   {"password": "CL34R_4P1!26", "groups": ["API_Users"],   "sid": "S-1-5-21-99999-11111-22222-1001"},
        "clearinghouse\\db_admin":  {"password": "DB_4dm1n!26",  "groups": ["DBA_Group"],   "sid": "S-1-5-21-99999-11111-22222-1002"},
        "clearinghouse\\forest_da": {"password": "F0r3st_D4!26", "groups": ["Domain_Admins"],"sid":"S-1-5-21-99999-11111-22222-500"},
    }

    FOREST_B_FLAGS = {
        "SID_HISTORY_PIVOT": "QA{s1d_h1st0ry_cr0ss_f0r3st_pwn3d_2026}",
        "FOREST_DA":         "QA{f0r3st_d0m41n_4dm1n_cl34r1ngh0us3_2026}",
    }

    # SID mapping: Forest A admin SID → Forest B trust SID
    TRUST_SID_MAP = {
        "S-1-5-21-31337-42424-99999-512":  "S-1-5-21-99999-11111-22222-512",  # Domain Admins
        "S-1-5-21-31337-42424-99999-1106": "S-1-5-21-99999-11111-22222-519",  # Enterprise Admins → EA in B
    }

    def __init__(self):
        self.trust_tickets: dict = {}  # session -> cross-forest TGT

    def request_cross_forest_tgt(self, session_token: str, user_sid: str) -> dict:
        """
        Simulate SID History attack: inject Forest A SID into Forest B TGT.
        Requires: user_sid must be Domain_Admins or Enterprise_Admins from Forest A.
        """
        mapped = self.TRUST_SID_MAP.get(user_sid)
        if not mapped:
            return {"error": "SID not trusted for cross-forest access"}

        ticket = {
            "type": "cross_forest_tgt",
            "src_forest": self.FOREST_A,
            "dst_forest": self.FOREST_B,
            "src_sid": user_sid,
            "dst_sid": mapped,
            "ticket_b64": base64.b64encode(
                secrets.token_bytes(128) + user_sid.encode()
            ).decode(),
            "expires": time.time() + 3600,
            "flag": self.FOREST_B_FLAGS["SID_HISTORY_PIVOT"],
        }
        self.trust_tickets[session_token] = ticket
        return ticket

    def enumerate_forest_b(self, session_token: str) -> dict:
        if session_token not in self.trust_tickets:
            return {"error": "No cross-forest ticket. Obtain one first."}
        ticket = self.trust_tickets[session_token]
        if time.time() > ticket["expires"]:
            return {"error": "Ticket expired"}
        return {
            "forest": self.FOREST_B,
            "users": list(self.FOREST_B_USERS.keys()),
            "dc": "clearing-dc-001.clearing-house.internal",
            "trusts": [{"direction": "inbound", "forest": self.FOREST_A}],
        }

    def compromise_forest_b_da(self, session_token: str, username: str, password: str) -> dict:
        if session_token not in self.trust_tickets:
            return {"error": "No cross-forest ticket"}
        user = self.FOREST_B_USERS.get(username)
        if not user:
            return {"error": "User not found in Forest B"}
        if user["password"] != password:
            return {"error": "Invalid credentials"}
        if "Domain_Admins" not in user.get("groups", []):
            return {"error": "User is not Domain Admin in Forest B"}
        return {
            "success": True,
            "message": f"Forest B compromised via {username}",
            "flag": self.FOREST_B_FLAGS["FOREST_DA"],
            "ntds_hint": "Extract NTDS.dit from clearing-dc-001 for all hashes."
        }

forest_trust = ForestTrust()


# ─────────────────────────────────────────────────────────────────────────────
#  7. REAL CRYPTO LAYER
# ─────────────────────────────────────────────────────────────────────────────

class CryptoOracles:
    """
    Real padding oracle (AES-CBC) and RSA PKCS1v1.5 signature confusion.
    """

    # Shared key — never exposed directly
    AES_KEY = hashlib.sha256(b"SSB_AES_CBC_2026").digest()[:16]
    AES_IV  = hashlib.sha256(b"SSB_AES_IV_2026").digest()[:16]

    # RSA-like (toy): encrypt = m^e mod n, sign = m^d mod n
    # Small primes for CTF (NOT real security)
    RSA_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF975221F100DE0F06  # fake large prime repr
    RSA_Q = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF000000000000000F
    # Use real hashlib-based substitute
    RSA_SIGN_KEY = hashlib.sha256(b"SSB_RSA_SIGN_2026").digest()

    def __init__(self):
        self.oracle_queries = defaultdict(int)  # ip -> query count
        self.oracle_lock    = threading.Lock()

    def encrypt_token(self, plaintext: str) -> str:
        ct = aes_cbc_encrypt(self.AES_KEY, self.AES_IV, plaintext.encode())
        return base64.b64encode(self.AES_IV + ct).decode()

    def decrypt_token(self, token_b64: str) -> tuple:
        """Returns (plaintext, padding_valid). Leaks padding validity — THE ORACLE."""
        try:
            raw = base64.b64decode(token_b64)
            iv  = raw[:16]
            ct  = raw[16:]
            pt  = aes_cbc_decrypt(self.AES_KEY, iv, ct)
            try:
                unpadded = pkcs7_unpad(pt)
                return unpadded.decode(errors="replace"), True
            except ValueError:
                return None, False   # padding error leaked!
        except Exception:
            return None, False

    def padding_oracle_query(self, ip: str, token_b64: str) -> dict:
        """
        The actual oracle endpoint.
        Returns different errors for padding vs MAC failure — enables CBC bit-flip attack.
        """
        with self.oracle_lock:
            self.oracle_queries[ip] += 1
            if self.oracle_queries[ip] > 5000:
                return {"error": "Oracle rate limit exceeded"}

        plaintext, padding_ok = self.decrypt_token(token_b64)
        if not padding_ok:
            return {"error": "Padding error", "code": "PADDING_INVALID"}   # oracle leak!
        if plaintext is None:
            return {"error": "Decryption error", "code": "DECRYPT_FAILED"}
        # Don't return plaintext — player must deduce it via oracle
        return {"status": "ok", "code": "PADDING_VALID"}

    def forge_admin_token(self, forged_plaintext: str) -> str:
        """If player correctly forges a token via padding oracle, give flag."""
        ct = aes_cbc_encrypt(self.AES_KEY, self.AES_IV, forged_plaintext.encode())
        return base64.b64encode(self.AES_IV + ct).decode()

    def rsa_sign(self, message: bytes) -> str:
        """Sign using HMAC-SHA256 as RSA substitute."""
        sig = hmac.new(self.RSA_SIGN_KEY, message, hashlib.sha256).digest()
        return base64.b64encode(sig).decode()

    def rsa_verify_confused(self, message: bytes, signature_b64: str) -> dict:
        """
        PKCS1v1.5 signature confusion vulnerability.
        Incorrectly verifies: only checks prefix, not full signature.
        Player can forge by prepending correct ASN.1 DigestInfo prefix.
        """
        try:
            sig = base64.b64decode(signature_b64)
        except Exception:
            return {"valid": False, "error": "bad base64"}

        expected = hmac.new(self.RSA_SIGN_KEY, message, hashlib.sha256).digest()
        # BUG: only check first 8 bytes (like the classic Bleichenbacher'06 attack)
        if sig[:8] == expected[:8]:
            return {"valid": True, "confused": True}
        return {"valid": False}

crypto_oracles = CryptoOracles()


# ─────────────────────────────────────────────────────────────────────────────
#  ROUTE REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────

# ── Advanced Routes (inlined) ──────────────────────────────────────────────

# ── ML EDR middleware ─────────────────────────────────────────────────────
@app.before_request
def ml_edr_middleware():
    ip      = request.remote_addr or "0.0.0.0"
    ep      = request.path
    psize   = request.content_length or 0
    score   = ml_edr.score(ip, ep, psize, False, time.time())
    if score > BehavioralEDR.THRESHOLD * 1.5:
        # Very high anomaly → hard block
        return jsonify({"error": "EDR: Behavioral anomaly detected. Session terminated.",
                        "edr_score": round(score, 2)}), 403

# ── Entry: SQLi ───────────────────────────────────────────────────────────
@app.route("/portal/search", methods=["POST"])
def portal_search():
    """User search — vulnerable to UNION-based SQLi."""
    data  = request.get_json(silent=True) or {}
    query = data.get("username", "")
    rows  = _sqli_query(query)
    if rows and rows[0].get("role") == "staff":
        return jsonify({"results": rows,
                        "hint": "Staff credentials found. Try /portal/login."})
    return jsonify({"results": rows})

# ── Entry: SSRF ───────────────────────────────────────────────────────────
@app.route("/portal/fetch", methods=["POST"])
def portal_fetch():
    """Internal URL fetcher — vulnerable to SSRF."""
    data = request.get_json(silent=True) or {}
    url  = data.get("url", "")
    if not url:
        return jsonify({"error": "url required"}), 400
    result = _ssrf_fetch(url)
    return jsonify(result)

# ── Entry: SSTI/RCE ──────────────────────────────────────────────────────
@app.route("/portal/render", methods=["POST"])
def portal_render():
    """Template renderer — vulnerable to SSTI."""
    data     = request.get_json(silent=True) or {}
    template = data.get("template", "welcome")
    user_in  = data.get("input", "")
    result   = _rce_render(template, user_in)
    return jsonify(result)

# ── Kerberos: AS-REQ (AS-REP Roasting) ───────────────────────────────────
@app.route("/kerberos/as-req", methods=["POST"])
def kerberos_as_req():
    """
    Kerberos AS-REQ endpoint.
    If target user has preauth disabled → return crackable AS-REP hash.
    """
    data     = request.get_json(silent=True) or {}
    username = data.get("username", "")
    preauth  = data.get("preauth_data", None)   # None = no preauth sent

    user = KERBEROS_USERS.get(username)
    if not user:
        return jsonify({"error": "KDC_ERR_C_PRINCIPAL_UNKNOWN"}), 404

    if user["preauth"] and preauth is None:
        return jsonify({"error": "KDC_ERR_PREAUTH_REQUIRED",
                        "etype": [23, 18, 17]}), 400

    session_key = _nt_hash(user["password"])
    ticket      = _build_asrep_ticket(username, session_key)
    return jsonify({
        "msg_type": "AS-REP",
        "etype": 23,
        "username": username,
        "ticket": ticket,
        "note": "$krb5asrep$23$" + username + "@SWEET-STRIKE-BANK.LOCAL:" + ticket[:32] + "$" + ticket[32:]
    })

# ── Kerberos: TGS-REQ (Kerberoasting) ────────────────────────────────────
@app.route("/kerberos/tgs-req", methods=["POST"])
def kerberos_tgs_req():
    """
    Kerberos TGS-REQ — request service ticket for an SPN.
    Returns crackable TGS hash.
    """
    data     = request.get_json(silent=True) or {}
    spn      = data.get("spn", "")
    tgt_b64  = data.get("tgt", "")

    if not tgt_b64:
        return jsonify({"error": "TGT required"}), 400

    # Find service account with this SPN
    target_user = None
    for uname, udata in KERBEROS_USERS.items():
        if udata.get("spn") == spn:
            target_user = (uname, udata)
            break

    if not target_user:
        return jsonify({"error": "KDC_ERR_S_PRINCIPAL_UNKNOWN"}), 404

    uname, udata = target_user
    svc_key      = _nt_hash(udata["password"])
    ticket       = _build_tgs_ticket(spn, svc_key)
    return jsonify({
        "msg_type": "TGS-REP",
        "etype": 23,
        "spn": spn,
        "ticket": ticket,
        "note": "$krb5tgs$23$*" + uname + "$SWEET-STRIKE-BANK.LOCAL$" + spn + "*$" + ticket[:32] + "$" + ticket[32:]
    })

# ── Kerberos: Crack verify ────────────────────────────────────────────────
@app.route("/kerberos/crack-verify", methods=["POST"])
def kerberos_crack_verify():
    """Verify if a cracked password matches a Kerberos ticket."""
    data     = request.get_json(silent=True) or {}
    username = data.get("username", "")
    ticket   = data.get("ticket", "")
    password = data.get("password", "")
    if _verify_krb_crack(username, ticket, password):
        return jsonify({"valid": True, "password": password,
                        "nt_hash": _nt_hash(password).hex()})
    return jsonify({"valid": False})

# ── C2: Binary over HTTP ──────────────────────────────────────────────────
@app.route("/c2/beacon", methods=["POST"])
def c2_beacon():
    raw = request.get_data()
    if not raw:
        body = request.get_json(silent=True) or {}
        raw  = base64.b64decode(body.get("data", ""))
    frame = c2_parse_frame(raw)
    if "error" in frame:
        return jsonify(frame), 400
    resp  = c2_handle(frame, state.ad)
    return Response(base64.b64encode(resp),
                    mimetype="application/octet-stream")

@app.route("/c2/sessions", methods=["GET"])
def c2_sessions():
    return jsonify({sid: {k: v for k, v in s.items() if k != "implant_key"}
                    for sid, s in C2_SESSIONS.items()})

# ── C2: DNS tunnel ────────────────────────────────────────────────────────
@app.route("/dns/query", methods=["POST"])
def dns_query():
    """Simulate DNS tunnel query processing."""
    data    = request.get_json(silent=True) or {}
    qname   = data.get("qname", "")
    session = data.get("session", "default")
    seq     = data.get("seq", 0)

    decoded = dns_decode_query(qname)
    if decoded:
        DNS_TUNNEL_STORE[session].append((seq, decoded))
        return jsonify({"status": "queued", "seq": seq, "bytes": len(decoded)})
    return jsonify({"error": "invalid dns query"}), 400

@app.route("/dns/reassemble", methods=["POST"])
def dns_reassemble():
    data    = request.get_json(silent=True) or {}
    session = data.get("session", "default")
    result  = dns_tunnel_reassemble(session)
    frame   = c2_parse_frame(result) if len(result) >= 16 else {"raw": result.hex()}
    return jsonify({"bytes": len(result), "frame": frame})

# ── C2: WebSocket framing ─────────────────────────────────────────────────
@app.route("/c2/ws-frame", methods=["POST"])
def c2_ws_frame():
    body = request.get_json(silent=True) or {}
    raw  = base64.b64decode(body.get("frame", ""))
    parsed = ws_parse_frame(raw)
    if "error" in parsed:
        return jsonify(parsed), 400
    sid  = parsed["session_id"]
    if sid not in WS_SESSIONS:
        WS_SESSIONS[sid] = {"id": sid, "tasks": []}
    resp_payload = json.dumps({"ack": True, "session": sid}).encode()
    resp_frame   = ws_build_frame(0x02, sid, resp_payload)
    return jsonify({"response": base64.b64encode(resp_frame).decode(),
                    "parsed": {**parsed, "payload": parsed["payload"].decode(errors="replace")}})

# ── C2: ICMP-over-HTTP ────────────────────────────────────────────────────
@app.route("/c2/icmp", methods=["POST"])
def c2_icmp():
    body = request.get_json(silent=True) or {}
    return jsonify(icmp_handle(body))

# ── ADCS: Protocol negotiation ────────────────────────────────────────────
@app.route("/adcs/proto/negotiate", methods=["POST"])
def adcs_proto_negotiate():
    """
    Client sends supported protocol versions + encoding preferences.
    Server picks a stack. No docs provided — player must figure this out.
    """
    data     = request.get_json(silent=True) or {}
    versions = data.get("versions", [])
    encodings= data.get("encodings", [])

    # Server requires: version=0x0300, encoding stack = [binary, xor, asn1, proto]
    required_version  = PROTO_VERSION
    required_encodings= ["binary", "xor", "asn1", "proto"]

    if required_version not in versions:
        return jsonify({"error": "unsupported version",
                        "supported": [required_version]}), 400

    selected = [e for e in required_encodings if e in encodings]
    if len(selected) < 2:
        return jsonify({"error": "insufficient encoding support",
                        "required_any_two": required_encodings}), 400

    nonce    = secrets.token_bytes(16)
    nego_id  = secrets.token_hex(8)
    return jsonify({
        "nego_id": nego_id,
        "version": required_version,
        "selected_encodings": selected,
        "nonce": nonce.hex(),
        "server_hello": base64.b64encode(
            struct.pack(">HH16s", required_version, len(selected),
                        nonce) + json.dumps(selected).encode()
        ).decode()
    })

@app.route("/adcs/proto/enroll", methods=["POST"])
def adcs_proto_enroll():
    """
    Multi-layer ADCS enrollment.
    Expects: base64(proto_encoded(xor_encoded(asn1_csr)))
    Session key derived from nego nonce XOR session_token hash.
    """
    data         = request.get_json(silent=True) or {}
    session_token= data.get("session_token", "")
    nonce_hex    = data.get("nonce", "")
    payload_b64  = data.get("payload", "")
    encoding     = data.get("encoding", "binary")  # which encoding layer

    if not session_token or not payload_b64:
        return jsonify({"error": "session_token and payload required"}), 400

    try:
        nonce   = bytes.fromhex(nonce_hex) if nonce_hex else b"\x00"*16
        raw     = base64.b64decode(payload_b64)
    except Exception as e:
        return jsonify({"error": f"decode error: {e}"}), 400

    # Decode based on selected encoding
    if encoding == "binary":
        session_key = hashlib.sha256(session_token.encode() + nonce).digest()[:16]
        parsed = adcs_parse_enroll_request(raw, session_key)
    elif encoding == "xor":
        decoded_dict = adcs_xor_decode(session_token, nonce, payload_b64.encode())
        if "error" in decoded_dict:
            return jsonify(decoded_dict), 400
        parsed = {"template": decoded_dict.get("template", ""),
                  "subject":  decoded_dict.get("subject", ""),
                  "san":      decoded_dict.get("san", ""),
                  "request_id": decoded_dict.get("id", 0)}
    elif encoding == "asn1":
        parsed = parse_csr_der(raw)
        parsed["template"] = data.get("template", "")
        parsed["san"]      = data.get("san", "")
    elif encoding == "proto":
        decoded = proto_decode(raw)
        parsed  = {"template":   decoded.get(1, ""),
                   "subject":    decoded.get(2, ""),
                   "san":        decoded.get(3, ""),
                   "request_id": decoded.get(4, 0)}
    else:
        return jsonify({"error": f"unknown encoding: {encoding}"}), 400

    if "error" in parsed:
        return jsonify(parsed), 400

    # Forward to main ADCS engine if available
    if hasattr(state, "ca") and state.ca:
        # Build a fake ADCS session
        fake_session = {
            "id": session_token,
            "groups": state.web_sessions.get(session_token, {}).get("groups", []),
            "ip": request.remote_addr or "0.0.0.0",
        }
        result = state.ca.request_cert(
            fake_session,
            parsed.get("template", ""),
            parsed.get("subject", ""),
            san=parsed.get("san") or None,
            protocol_data={"version": PROTO_VERSION, "valid": True}
        )
        return jsonify(result)

    return jsonify({"parsed": parsed, "status": "accepted",
                    "note": "Forwarded to CA engine."})

# ── Crypto: Padding oracle ────────────────────────────────────────────────
@app.route("/crypto/oracle", methods=["POST"])
def crypto_oracle():
    """AES-CBC padding oracle."""
    data  = request.get_json(silent=True) or {}
    token = data.get("token", "")
    ip    = request.remote_addr or "0.0.0.0"
    return jsonify(crypto_oracles.padding_oracle_query(ip, token))

@app.route("/crypto/token", methods=["POST"])
def crypto_token():
    """Get an encrypted token for a given plaintext (limited)."""
    data      = request.get_json(silent=True) or {}
    plaintext = data.get("plaintext", "guest")
    # Only allow non-admin plaintexts here
    if any(x in plaintext.lower() for x in ("admin", "staff", "a.jones", "root")):
        return jsonify({"error": "Forbidden plaintext"}), 403
    token = crypto_oracles.encrypt_token(plaintext)
    return jsonify({"token": token})

@app.route("/crypto/verify-token", methods=["POST"])
def crypto_verify_token():
    """Verify a forged admin token — gives flag if correct."""
    data  = request.get_json(silent=True) or {}
    token = data.get("token", "")
    pt, ok = crypto_oracles.decrypt_token(token)
    if ok and pt and "admin" in pt.lower():
        return jsonify({"valid": True,
                        "flag": "QA{p4dd1ng_0r4cl3_4es_cbc_f0rg3d_2026}",
                        "plaintext": pt})
    return jsonify({"valid": False, "padding_ok": ok})

@app.route("/crypto/rsa-sign", methods=["POST"])
def crypto_rsa_sign():
    data    = request.get_json(silent=True) or {}
    message = data.get("message", "").encode()
    sig     = crypto_oracles.rsa_sign(message)
    return jsonify({"signature": sig})

@app.route("/crypto/rsa-verify", methods=["POST"])
def crypto_rsa_verify():
    """RSA signature confusion — only checks prefix."""
    data    = request.get_json(silent=True) or {}
    message = data.get("message", "").encode()
    sig     = data.get("signature", "")
    result  = crypto_oracles.rsa_verify_confused(message, sig)
    if result.get("valid") and result.get("confused"):
        return jsonify({**result,
                        "flag": "QA{pkcs1v15_s1gn4tur3_c0nfus10n_2026}"})
    return jsonify(result)

# ── Forest Trust ──────────────────────────────────────────────────────────
@app.route("/forest/trust-ticket", methods=["POST"])
def forest_trust_ticket():
    data      = request.get_json(silent=True) or {}
    session   = data.get("session_token", "")
    user_sid  = data.get("user_sid", "")
    return jsonify(forest_trust.request_cross_forest_tgt(session, user_sid))

@app.route("/forest/enumerate", methods=["POST"])
def forest_enumerate():
    data    = request.get_json(silent=True) or {}
    session = data.get("session_token", "")
    return jsonify(forest_trust.enumerate_forest_b(session))

@app.route("/forest/compromise", methods=["POST"])
def forest_compromise():
    data     = request.get_json(silent=True) or {}
    session  = data.get("session_token", "")
    username = data.get("username", "")
    password = data.get("password", "")
    return jsonify(forest_trust.compromise_forest_b_da(session, username, password))

# ── ML EDR status ─────────────────────────────────────────────────────────
@app.route("/edr/ml-profile", methods=["GET"])
def edr_ml_profile():
    ip = request.remote_addr or "0.0.0.0"
    return jsonify(ml_edr.get_profile(ip))

logging.info("[advanced_modules] All routes registered.")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 31337))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
