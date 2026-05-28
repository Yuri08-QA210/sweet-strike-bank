# Sweet-Strike Bank CTF Challenge

**Author:** QA210
**Difficulty:** Hard
**Category:** Web / ADCS / Core Banking / Crypto
**Flag Format:** `QA{...}`

## Overview

Sweet-Strike Bank is a mega CTF challenge combining multiple attack domains:

- **Web Exploitation** — Race Condition / IDOR / Case-sensitivity bypass
- **Graph-Based Network Pivoting** — 1000-machine network with VLANs and ACLs
- **Active Directory / ADCS** — ESC7 + ESC11 + Shadow Credentials + SAN abuse
- **Core Banking / SWIFT** — HSM padding oracle + logic flaw
- **EDR Sentinel** — Zero-Trust behavioral analysis, timing checks, honey-tokens
- **Anti-Automation** — Custom protocol v3, PoW, trojanized tools
- **Golden Certificate Persistence** — Time-limited flag

## 9 Flags

| # | Flag Name | Attack Vector |
|---|-----------|--------------|
| 1 | FLAG_WEB | Race Condition / IDOR on account opening |
| 2 | FLAG_PIVOT | Network pivoting through 1000-node graph |
| 3 | FLAG_ADCS_ESC7 | ESC7 - Enable EDITF_ATTRIBUTESUBJECTALTNAME2 |
| 4 | FLAG_SHADOW_CREDS | Shadow Credentials - msDS-KeyCredentialLink |
| 5 | FLAG_ESC11_RELAY | ESC11 - ICERTPassage RPC relay |
| 6 | FLAG_SAN_FLAG | SAN abuse after EDITF flag enabled |
| 7 | FLAG_SWIFT | SWIFT HSM padding oracle + logic flaw |
| 8 | FLAG_GOLDEN | Golden Certificate persistence |
| 9 | FLAG_NTDS | NTDS.dit extraction via DC machine cert |

## Certipy/Impacket Auto-Tool Detection

The challenge includes simulated Certipy and Impacket auto-tool endpoints. Players who use these tools will be **marked as compromised** in the EDR system. A skull indicator (☠) with pulse animation appears in the EDR panel for compromised users.

Using auto-tools results in receiving **fake flags** instead of real ones.

## Deployment

### Docker (Recommended)

```bash
docker-compose up --build -d
```

The challenge will be available at `http://localhost:31337`

### Manual

```bash
cd backend
pip install -r requirements.txt
python app.py
```

## Structure

```
sweet-strike-bank/
├── backend/
│   ├── app.py              # Flask backend (all challenge logic)
│   └── requirements.txt    # Python dependencies
├── frontend/
│   ├── index.html          # SPA landing/login/dashboard
│   ├── css/
│   │   └── style.css       # Dark emerald/gold theme
│   └── js/
│       └── app.js          # All frontend logic
├── Dockerfile
├── docker-compose.yml
├── solve.py                # Author's solution script
└── README.md               # This file
```

## Entry Points

| Endpoint | Description |
|----------|-------------|
| `/` | Frontend SPA |
| `/web/login` | Web portal login |
| `/web/account/open` | Account opening (Race Condition here) |
| `/adcs/auth` | ADCS authentication (requires PoW) |
| `/adcs/templates` | Certificate templates |
| `/adcs/cert/request` | Certificate request (custom protocol v3) |
| `/adcs/ca/manage` | CA management (ESC7) |
| `/adcs/shadow-creds` | Shadow Credentials attack |
| `/adcs/relay` | ESC11 relay |
| `/adcs/golden` | Golden Certificate |
| `/swift/hsm/init` | HSM session initialization |
| `/swift/hsm/sign` | HSM signing (padding oracle) |
| `/swift/transfer` | SWIFT transfer (logic flaw) |
| `/tunnel/` | Steganographic tunnel |
| `/edr/` | EDR status |
| `/api/autotool/certipy` | Auto-tool: Certipy (TRAP!) |
| `/api/autotool/impacket` | Auto-tool: Impacket (TRAP!) |
| `/api/compromised-users` | Compromised users tracker |

## Hints

1. The web portal has a case-sensitivity bug in account type validation
2. The ADCS uses a custom protocol v3 — standard MS-WCCE tools won't work
3. EDR bypass requires: custom User-Agent, timing delays (0.5-2s), non-optimal paths
4. The Zero-Trust key rotates every 60 seconds: `SHA256("Pentagon_Who?_" + minute)`
5. MFA bypass header: `X-ZT-Verify: <zero_trust_key>`
6. The HSM leaks padding validity — this is an oracle
7. SWIFT transfer doesn't verify signature matches transfer data
8. Some users and tools are honey-tokens — they lead to fake flags
9. Downloading "certify_v2.exe" marks your session as compromised
