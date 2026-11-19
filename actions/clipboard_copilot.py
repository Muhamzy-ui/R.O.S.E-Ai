"""
Clipboard AI Co-Pilot for R.O.S.E.
Reads whatever text/code/message/error the user copied, transforms it with Gemini
(write a reply, summarize, fix code/error, rewrite/polish, or translate),
places the result right back onto the Windows clipboard (ready for Ctrl+V),
and tracks recent clipboard outputs in memory/clipboard_history.json.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from app_config import get_app_config_value

BASE_DIR = Path(__file__).resolve().parent.parent
HISTORY_FILE = BASE_DIR / "memory" / "clipboard_history.json"
_NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def read_clipboard() -> str:
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=_NO_WIN,
        )
        return (res.stdout or "").rstrip("\r\n")
    except Exception:
        return ""


def write_clipboard(text: str) -> bool:
    if text is None:
        return False
    try:
        subprocess.run(
            ["clip"],
            input=str(text).encode("utf-16-le"),
            capture_output=True,
            timeout=4,
            creationflags=_NO_WIN,
        )
        return True
    except Exception:
        return False


def _save_history(kind: str, source_preview: str, result_text: str) -> None:
    try:
        items = json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.exists() else []
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []
    items.append({
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "action": kind,
        "source": source_preview[:180],
        "output": result_text[:600],
    })
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(items[-25:], ensure_ascii=False, indent=2), encoding="utf-8")


def _ask_gemini(system_instruction: str, user_text: str) -> str:
    api_key = str(get_app_config_value("gemini_api_key", "") or "").strip()
    if not api_key:
        return ""
    try:
        from app_config import generate_gemini_content
        resp = generate_gemini_content(
            api_key,
            contents=f"{system_instruction}\n\n--- INPUT ---\n{user_text[:8000]}",
        )
        return (getattr(resp, "text", "") or "").strip()
    except Exception:
        return ""


def clipboard_copilot(
    action: str = "read",
    instruction: str = "",
    copy_back: bool = True,
) -> str:
    """
    Actions:
    - read: Returns what is currently on the Windows clipboard
    - reply: Drafts a polished professional reply to the copied email/WhatsApp/message and puts it on clipboard
    - summarize: Summarizes copied long text/chat into concise bullet points
    - fix_code: Diagnoses and fixes a copied error/traceback/code snippet and puts fixed code on clipboard
    - rewrite: Polishes grammar, clarity, or tone (e.g. formal, concise, friendly) and copies back
    - translate: Translates copied text to requested language (defaults to polished English)
    - history: Shows recent clipboard AI results
    """
    act = (action or "read").strip().lower()
    note = (instruction or "").strip()

    if act == "history":
        if not HISTORY_FILE.exists():
            return "No clipboard AI history recorded yet."
        try:
            items = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            items = []
        if not items:
            return "No clipboard AI history recorded yet."
        lines = [f"Last {min(5, len(items))} clipboard actions:"]
        for it in reversed(items[-5:]):
            lines.append(f"- [{it.get('timestamp')}] {it.get('action')}: {it.get('output', '')[:100]}...")
        return "\n".join(lines)

    clip = read_clipboard()
    if not clip.strip():
        return "Your clipboard is currently empty. Copy some text, a message, or an error first."

    if act == "read":
        preview = clip[:1800]
        return f"Clipboard content ({len(clip)} chars):\n{preview}"

    prompts = {
        "reply": (
            "Write a clear, natural, polished professional reply to the message below. "
            + (f"Specific direction: {note}. " if note else "")
            + "Return ONLY the ready-to-send reply text with no extra commentary."
        ),
        "summarize": (
            "Summarize the text below into 3 to 5 crisp executive bullet points with key takeaways and any action items. "
            + (f"Focus on: {note}." if note else "")
        ),
        "fix_code": (
            "Analyze the code or error traceback below. Explain the root cause in 1 short sentence, then provide the clean corrected code/fix. "
            + (f"Context: {note}." if note else "")
        ),
        "rewrite": (
            "Rewrite and polish the text below into crisp, grammatical, professional English. "
            + (f"Tone/goal: {note}. " if note else "")
            + "Return ONLY the improved text ready to paste."
        ),
        "translate": (
            f"Translate the text below accurately into {note or 'polished English'}. Return ONLY the translation."
        ),
    }

    prompt = prompts.get(act)
    if not prompt:
        return f"Unknown clipboard_copilot action: {action}"

    output = _ask_gemini(prompt, clip)
    if not output:
        return "Could not process clipboard text right now (check Gemini API connectivity)."

    _save_history(act, clip, output)
    copied = False
    if copy_back:
        # For fix_code, keep clipboard clean with the fixed snippet if fenced code exists
        to_copy = output
        if act == "fix_code" and "```" in output:
            blocks = output.split("```")
            if len(blocks) >= 3:
                code_block = blocks[1]
                if "\n" in code_block:
                    first_line, rest = code_block.split("\n", 1)
                    if len(first_line.strip()) <= 15 and " " not in first_line.strip():
                        code_block = rest
                to_copy = code_block.strip()
        copied = write_clipboard(to_copy)

    status = " (copied straight to your clipboard — press Ctrl+V to paste)" if copied else ""
    return f"{output}{status}"

