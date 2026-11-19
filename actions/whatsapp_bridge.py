"""
R.O.S.E (Project Mirror) - WhatsApp Bridge & Baileys Client (Phase 2)
Lightweight Node.js Sidecar Manager & HTTP API Client
Rules:
- A2: Truthful results, server ACK required
- A3: Strict schemas, rapidfuzz name matching, no guessing
- A5: Localhost only (127.0.0.1:48201)
"""

from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import rapidfuzz
from rapidfuzz import fuzz

from core.audit import audit_log
from core.logging_setup import get_logger
from core.permissions import Tier
from core.result import Result

logger = get_logger("whatsapp_bridge")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SIDECAR_DIR = PROJECT_ROOT / "sidecars" / "whatsapp"
AUTH_DIR = PROJECT_ROOT / "whatsapp_auth"
PORT = int(os.environ.get("ROSE_WHATSAPP_PORT", "48201"))
AUTH_TOKEN = os.environ.get("ROSE_WHATSAPP_TOKEN", "rose_wa_secret_token_default")
BASE_URL = f"http://127.0.0.1:{PORT}"

_SIDECAR_PROCESS: Optional[subprocess.Popen] = None
_SIDECAR_LOG_HANDLE: Optional[Any] = None
_PROCESS_LOCK = threading.Lock()
_MOCK_MODE = False
_MOCK_DATA: Dict[str, Any] = {
    "status": {"ok": True, "connected": True, "paired": True, "user": {"id": "2348000000000"}},
    "chats": [
        {"jid": "2348011111111@s.whatsapp.net", "name": "Aisha Bello", "phone": "2348011111111"},
        {"jid": "2348022222222@s.whatsapp.net", "name": "Aisha Musa", "phone": "2348022222222"},
        {"jid": "2348033333333@s.whatsapp.net", "name": "Tunde Alabi", "phone": "2348033333333"},
        {"jid": "2348044444444@s.whatsapp.net", "name": "Mom", "phone": "2348044444444"},
        {"jid": "1203630222222@g.us", "name": "Team Dev Group", "phone": "1203630222222"},
    ],
    "unread": [
        {"chat": "Aisha Bello", "unreadCount": 1, "lastMessage": "Are we still meeting?"},
        {"chat": "Team Dev Group", "unreadCount": 5, "lastMessage": "PR #42 approved"},
        {"chat": "Mom", "unreadCount": 1, "lastMessage": "Call me"},
    ],
}


def set_mock_mode(enabled: bool = True) -> None:
    global _MOCK_MODE
    _MOCK_MODE = enabled


def is_mock_mode() -> bool:
    return _MOCK_MODE


# ── Phone Number Normalization (Nigeria +234 & International) ───────────────
def normalize_phone(phone_input: str) -> str:
    """
    Cleans and standardizes phone numbers:
    - Nigeria 11 digits starting with 0 (e.g. 0803..., 090..., 081..., 070...) -> 234 + 10 digits
    - International numbers: strips symbols, verifies length between 8 and 15 digits.
    """
    digits = re.sub(r"\D+", "", str(phone_input or "").strip())
    if len(digits) == 11 and digits.startswith("0"):
        digits = "234" + digits[1:]
    elif len(digits) == 10 and digits.startswith("8") or digits.startswith("7") or digits.startswith("9"):
        digits = "234" + digits

    if len(digits) < 8 or len(digits) > 15:
        raise ValueError(f"Invalid phone number format: '{phone_input}'. Valid format e.g. 08031234567 or +2348031234567")
    return digits


# ── HTTP API Client ──────────────────────────────────────────────────────────
def _request(method: str, endpoint: str, data: Optional[Dict[str, Any]] = None, timeout: float = 12.0) -> Dict[str, Any]:
    if _MOCK_MODE:
        if endpoint == "/status":
            return _MOCK_DATA["status"]
        elif endpoint.startswith("/chats"):
            return {"ok": True, "chats": _MOCK_DATA["chats"]}
        elif endpoint == "/unread":
            return {"ok": True, "unread": _MOCK_DATA["unread"]}
        elif endpoint == "/on-whatsapp":
            phone = (data or {}).get("phone", "")
            return {"ok": True, "exists": bool(phone), "jid": f"{phone}@s.whatsapp.net"}
        elif endpoint == "/send":
            # Simulate ACK
            return {"ok": True, "verified": True, "messageId": f"MOCK_MSG_{int(time.time())}"}
        return {"ok": True}

    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            resp_body = response.read().decode("utf-8")
            return json.loads(resp_body) if resp_body else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            parsed = json.loads(raw)
            return parsed
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Process Lifecycle ────────────────────────────────────────────────────────
def is_node_installed() -> bool:
    return shutil.which("node") is not None


def is_sidecar_running() -> bool:
    if _MOCK_MODE:
        return True
    try:
        st = _request("GET", "/status", timeout=1.5)
        return bool(st.get("ok"))
    except Exception:
        return False


def start_sidecar() -> Result:
    """Launches the Node.js Baileys sidecar if not already running."""
    if _MOCK_MODE:
        return Result.success(data={"mock": True}, verified=True, message="Sidecar running (mock mode).")

    if not is_node_installed():
        err = (
            "Node.js is not installed on this computer. "
            "Please install Node.js LTS from https://nodejs.org/ to enable WhatsApp integration."
        )
        logger.error(err)
        return Result.failure(err)

    if is_sidecar_running():
        return Result.success(data={"port": PORT}, verified=True, message="Sidecar is already running.")

    global _SIDECAR_PROCESS, _SIDECAR_LOG_HANDLE
    already_spawned = False
    with _PROCESS_LOCK:
        if _SIDECAR_PROCESS is not None and _SIDECAR_PROCESS.poll() is None:
            already_spawned = True
        else:
            server_script = SIDECAR_DIR / "server.js"
            if not server_script.exists():
                return Result.failure(f"Sidecar server file missing: {server_script}")

            env = os.environ.copy()
            env["ROSE_WHATSAPP_PORT"] = str(PORT)
            env["ROSE_WHATSAPP_TOKEN"] = AUTH_TOKEN

            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

            log_dir = PROJECT_ROOT / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            if _SIDECAR_LOG_HANDLE is not None:
                try:
                    _SIDECAR_LOG_HANDLE.close()
                except Exception:
                    pass
                _SIDECAR_LOG_HANDLE = None

            try:
                _SIDECAR_LOG_HANDLE = open(log_dir / "whatsapp_sidecar.log", "a", encoding="utf-8")
                _SIDECAR_PROCESS = subprocess.Popen(
                    ["node", str(server_script)],
                    cwd=str(SIDECAR_DIR),
                    env=env,
                    stdout=_SIDECAR_LOG_HANDLE,
                    stderr=subprocess.STDOUT,
                    creationflags=creation_flags,
                )
            except Exception as e:
                if _SIDECAR_LOG_HANDLE is not None:
                    try:
                        _SIDECAR_LOG_HANDLE.close()
                    except Exception:
                        pass
                    _SIDECAR_LOG_HANDLE = None
                return Result.failure(f"Failed to spawn WhatsApp sidecar: {e}")

    # Wait up to 6s for HTTP listener to bind (even when another thread spawned it)
    for _ in range(15):
        if is_sidecar_running():
            msg = "Sidecar running." if already_spawned else "WhatsApp sidecar started."
            return Result.success(data={"port": PORT}, verified=True, message=msg)
        time.sleep(0.4)

    return Result.unverified(data={"port": PORT}, message="Sidecar launched; waiting for initial connection.")


def ensure_sidecar_started() -> Result:
    """Starts the Baileys sidecar on demand when live mode is active."""
    if _MOCK_MODE or is_sidecar_running():
        return Result.success(data={"port": PORT}, verified=True, message="Sidecar running.")
    return start_sidecar()


def stop_sidecar() -> None:
    """Stops the sidecar process cleanly."""
    global _SIDECAR_PROCESS, _SIDECAR_LOG_HANDLE
    with _PROCESS_LOCK:
        if is_sidecar_running():
            try:
                _request("POST", "/shutdown", timeout=2.0)
            except Exception:
                pass
        if _SIDECAR_PROCESS is not None:
            try:
                _SIDECAR_PROCESS.terminate()
                _SIDECAR_PROCESS.wait(timeout=2.0)
            except Exception:
                try:
                    _SIDECAR_PROCESS.kill()
                except Exception:
                    pass
            _SIDECAR_PROCESS = None
        if _SIDECAR_LOG_HANDLE is not None:
            try:
                _SIDECAR_LOG_HANDLE.close()
            except Exception:
                pass
            _SIDECAR_LOG_HANDLE = None


# ── Status & Pairing ─────────────────────────────────────────────────────────
def get_whatsapp_status() -> Dict[str, Any]:
    """Retrieves connection and pairing state from the sidecar."""
    st = _request("GET", "/status", timeout=3.0)
    if not st.get("ok"):
        return {
            "running": False,
            "connected": False,
            "paired": False,
            "qr": None,
            "qrDataUrl": None,
            "error": st.get("error", "Sidecar unreachable"),
        }
    return {
        "running": True,
        "connected": bool(st.get("connected")),
        "paired": bool(st.get("paired")),
        "qr": st.get("qr"),
        "qrDataUrl": st.get("qrDataUrl"),
        "user": st.get("user"),
    }


# ── Name Resolution with RapidFuzz ───────────────────────────────────────────
def get_all_contact_candidates() -> List[Dict[str, str]]:
    """Gathers contact records from Sidecar SQLite cache + memory phone book."""
    candidates: List[Dict[str, str]] = []
    seen_jids = set()

    # 1. From Sidecar
    chats_res = _request("GET", "/chats", timeout=3.0)
    if chats_res.get("ok"):
        for c in chats_res.get("chats", []):
            jid = c.get("jid", "")
            name = c.get("name") or c.get("push_name") or ""
            phone = c.get("phone") or jid.split("@")[0]
            if jid and jid not in seen_jids:
                seen_jids.add(jid)
                candidates.append({"name": name, "phone": phone, "jid": jid, "source": "whatsapp_cache"})

    # 2. From local memory phone book
    phone_book_file = PROJECT_ROOT / "memory" / "phone_book.json"
    if phone_book_file.exists():
        try:
            pb = json.loads(phone_book_file.read_text(encoding="utf-8"))
            for key, val in pb.items():
                if isinstance(val, dict):
                    disp = val.get("display_name", key)
                    phone = str(val.get("phone_number", ""))
                    if phone and phone not in seen_jids:
                        candidates.append({"name": disp, "phone": phone, "jid": f"{phone}@s.whatsapp.net", "source": "phone_book"})
        except Exception:
            pass

    return candidates


def resolve_recipient_by_name(query: str) -> Tuple[Optional[Dict[str, str]], List[Dict[str, str]], str]:
    """
    Fuzzy-matches recipient name using rapidfuzz against saved contacts & push names.
    Returns:
    - (matched_record, [], "match") if exactly one strong match is found.
    - (None, [close_matches], "ambiguous") if several close matches exist (e.g. Aisha Bello vs Aisha Musa).
    - (None, [], "not_found") if no confident match is found.
    """
    clean_query = query.strip()
    if not clean_query:
        return None, [], "not_found"

    # If the user directly passed a phone number
    digit_test = re.sub(r"\D+", "", clean_query)
    if len(digit_test) >= 10:
        try:
            norm = normalize_phone(clean_query)
            return {"name": f"+{norm}", "phone": norm, "jid": f"{norm}@s.whatsapp.net"}, [], "match"
        except ValueError:
            pass

    candidates = get_all_contact_candidates()
    if not candidates:
        return None, [], "not_found"

    names = [c["name"] for c in candidates if c["name"]]
    if not names:
        return None, [], "not_found"

    # RapidFuzz extraction
    matches = rapidfuzz.process.extract(
        clean_query,
        names,
        scorer=fuzz.WRatio,
        limit=5,
    )

    # Filter matches above 68 threshold
    valid_matches = []
    for match_name, score, idx in matches:
        if score >= 68:
            matched_candidate = [c for c in candidates if c["name"] == match_name][0]
            valid_matches.append((matched_candidate, score))

    if not valid_matches:
        return None, [], "not_found"

    best_cand, best_score = valid_matches[0]

    # Check for ambiguity: multiple matches within 6 points of the top score
    close_matches = [cand for cand, sc in valid_matches if (best_score - sc) <= 6 and sc >= 72]

    if len(close_matches) > 1:
        # Check if they share the exact same phone number (duplicates)
        phones = {c["phone"] for c in close_matches}
        if len(phones) == 1:
            return close_matches[0], [], "match"
        return None, close_matches, "ambiguous"

    if best_score >= 72:
        return best_cand, [], "match"

    return None, [], "not_found"


# ── WhatsApp Number Verification (onWhatsApp) ────────────────────────────────
def verify_on_whatsapp(phone: str) -> Tuple[bool, Optional[str]]:
    """Checks with WhatsApp servers if the given phone number is registered."""
    try:
        norm = normalize_phone(phone)
    except ValueError:
        return False, None

    res = _request("POST", "/on-whatsapp", {"phone": norm}, timeout=6.0)
    if res.get("ok"):
        return bool(res.get("exists")), res.get("jid")
    return False, None


# ── Send Message with Server ACK (Rule A2) ────────────────────────────────────
def send_verified_message(
    recipient: str,
    message: str,
    confirmed: bool = False,
) -> Result:
    """
    Sends a message via Baileys with:
    1. Rapidfuzz name resolution
    2. Confirmation prompt (Tier 2)
    3. Server ACK verification (receipt check, timeout 10s)
    """
    clean_msg = str(message or "").strip()
    if not clean_msg:
        return Result.failure("Message content cannot be empty.")

    # 1. Resolve contact by name or number
    matched, close_matches, status = resolve_recipient_by_name(recipient)

    if status == "ambiguous":
        names_str = " or ".join(f"'{c['name']}' ({c['phone']})" for c in close_matches[:3])
        return Result.failure(
            f"I found multiple people named '{recipient}'. Did you mean {names_str}?",
            data={"ambiguous": True, "candidates": close_matches},
        )

    if status == "not_found" or not matched:
        return Result.failure(
            f"I couldn't find '{recipient}' in your WhatsApp contacts or phone book. "
            "Please provide their phone number so I can send the message.",
            data={"not_found": True, "query": recipient},
        )

    target_name = matched["name"]
    target_phone = matched["phone"]
    target_jid = matched.get("jid") or f"{target_phone}@s.whatsapp.net"

    # 2. Check connection status
    status_info = get_whatsapp_status()
    if not status_info.get("connected") and not _MOCK_MODE:
        return Result.failure("WhatsApp is not connected. Please scan the QR code first.")

    # 3. Verify on WhatsApp if new number
    if not _MOCK_MODE and matched.get("source") != "whatsapp_cache":
        exists, resolved_jid = verify_on_whatsapp(target_phone)
        if not exists:
            return Result.failure(f"The number {target_phone} ({target_name}) is not registered on WhatsApp.")
        if resolved_jid:
            target_jid = resolved_jid

    # 4. Confirmation Gate (Tier 2)
    if not confirmed:
        prompt_msg = f"Send '{clean_msg}' to {target_name} ({target_phone})?"
        return Result.failure(
            prompt_msg,
            data={"confirmation_required": True, "recipient": target_name, "phone": target_phone, "message": clean_msg},
        )

    # 5. Dispatch message and wait for Server ACK
    t0 = time.time()
    send_res = _request("POST", "/send", {"to": target_jid, "message": clean_msg}, timeout=12.0)
    dur = time.time() - t0

    if send_res.get("ok") and send_res.get("verified"):
        res = Result.success(
            data={
                "messageId": send_res.get("messageId"),
                "recipient": target_name,
                "phone": target_phone,
                "duration_s": round(dur, 2),
            },
            verified=True,
            message=f"Sent '{clean_msg}' to {target_name}. Server acknowledgment verified.",
        )
        audit_log("whatsapp_send", {"to": target_phone, "name": target_name}, Tier.TIER_2_CONFIRM, res, source="voice", confirmed_by_user=True)
        return res

    err = send_res.get("error") or "Message sent to queue but server hasn't acknowledged it yet"
    res = Result.failure(err, data={"verified": False, "to": target_phone})
    audit_log("whatsapp_send", {"to": target_phone, "name": target_name}, Tier.TIER_2_CONFIRM, res, source="voice", confirmed_by_user=True)
    return res


# ── Unread Message Summary Tool ───────────────────────────────────────────────
def get_unread_summary() -> Result:
    """Reads recent unread chats and formats a clean verbal/text summary."""
    status_info = get_whatsapp_status()
    if not status_info.get("connected") and not _MOCK_MODE:
        return Result.failure("WhatsApp is not connected right now.")

    unread_res = _request("GET", "/unread", timeout=4.0)
    if not unread_res.get("ok"):
        return Result.failure(unread_res.get("error", "Failed to retrieve unread messages."))

    unread_list = unread_res.get("unread", [])
    if not unread_list:
        return Result.success(data={"count": 0, "unread": []}, verified=True, message="You have no unread WhatsApp messages.")

    total_msgs = sum(item.get("unreadCount", 1) for item in unread_list)
    formatted_parts = []
    for item in unread_list[:5]:
        chat_name = item.get("chat", "Unknown")
        count = item.get("unreadCount", 1)
        last_msg = item.get("lastMessage", "")
        if count == 1 and last_msg:
            formatted_parts.append(f"{chat_name}: '{last_msg}'")
        else:
            formatted_parts.append(f"{chat_name}: {count} messages")

    summary_text = f"{len(unread_list)} unread chats ({total_msgs} messages): " + "; ".join(formatted_parts)
    return Result.success(
        data={"count": len(unread_list), "total_messages": total_msgs, "unread": unread_list},
        verified=True,
        message=summary_text,
    )


def _register_whatsapp_lifecycle_hooks() -> None:
    """Ensures WhatsApp sidecar stops immediately on power sleep or emergency kill switch."""
    try:
        from core.permissions import security_manager
        from core.power_control import register_service_stopper

        security_manager.register_kill_callback(stop_sidecar)
        register_service_stopper("whatsapp_sidecar", stop_sidecar)
    except Exception as exc:
        logger.warning(f"Failed to register WhatsApp lifecycle hooks: {exc}")


_register_whatsapp_lifecycle_hooks()


