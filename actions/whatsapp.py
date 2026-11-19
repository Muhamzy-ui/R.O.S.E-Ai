"""
R.O.S.E (Project Mirror) - WhatsApp Action Handler (Phase 2)
100% Baileys WebSocket Integration (No pyautogui, no pywhatkit, no fake success).
Rules:
- A2: Real verified delivery via server ACK
- A3: Rapidfuzz name resolution, Nigerian format normalization
- A5: Tier 2 Confirmation Gate
"""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional, Union

from core.result import Result
from actions.whatsapp_bridge import (
    get_whatsapp_status,
    get_unread_summary,
    normalize_phone,
    resolve_recipient_by_name,
    send_verified_message,
    start_sidecar,
)

BASE_DIR = Path(__file__).resolve().parent.parent
PHONEBOOK_FILE = BASE_DIR / "memory" / "phone_book.json"


def save_whatsapp_contact(display_name: str, phone_number: str, aliases: str = "") -> str:
    """Saves or updates a contact in the local contact book."""
    name = (display_name or "").strip()
    if not name:
        return "Contact display name is required."

    try:
        norm_phone = normalize_phone(phone_number)
    except ValueError as e:
        return str(e)

    PHONEBOOK_FILE.parent.mkdir(parents=True, exist_ok=True)
    pb = {}
    if PHONEBOOK_FILE.exists():
        try:
            pb = json.loads(PHONEBOOK_FILE.read_text(encoding="utf-8"))
        except Exception:
            pb = {}

    alias_list = [a.strip() for a in aliases.split(",") if a.strip()] if aliases else []
    pb[name] = {
        "display_name": name,
        "phone_number": norm_phone,
        "aliases": alias_list,
    }
    PHONEBOOK_FILE.write_text(json.dumps(pb, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"Saved WhatsApp contact '{name}' (+{norm_phone}) to phone book."


def send_whatsapp_message(
    message: str,
    recipient_name: str = "",
    phone_number: str = "",
    confirmed: bool = False,
    app_target: str = "auto",
    send_now: bool = True,
) -> str:
    """
    Production entry point for Gemini and R.O.S.E tool calling.
    - Resolves recipient by name using rapidfuzz against WhatsApp chats & phonebook
    - Enforces Tier 2 confirmation ("Send '...' to Aisha Bello?")
    - Waits for real server ACK before claiming success
    """
    clean_msg = (message or "").strip()
    target = (recipient_name or phone_number or "").strip()

    if not clean_msg:
        return "Message content cannot be empty."
    if not target:
        return "Please specify who you want to message (name or phone number)."

    res: Result = send_verified_message(
        recipient=target,
        message=clean_msg,
        confirmed=confirmed,
    )
    return res.to_user_message()


def get_whatsapp_unread() -> str:
    """Reads unread messages summary."""
    res: Result = get_unread_summary()
    return res.to_user_message()


def get_whatsapp_status_text() -> str:
    """Summarizes current connection and pairing status."""
    st = get_whatsapp_status()
    if not st.get("running"):
        return "WhatsApp sidecar is not running."
    if not st.get("paired"):
        return "WhatsApp is not paired yet. Please scan the QR code in the console or HUD."
    if not st.get("connected"):
        return "WhatsApp is paired but currently reconnecting to the server."
    user = st.get("user") or {}
    return f"WhatsApp is connected and active as {user.get('id', 'User')}."
