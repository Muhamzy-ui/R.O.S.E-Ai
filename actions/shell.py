"""
Terminal / PowerShell command execution on Windows with safety confirmation gate.
"""

import subprocess


# Critical OS-destroying commands that are never allowed
HARD_BLOCKED = [
    "format c:",
    "format /q",
    "del /f /s /q c:\\",
    "rd /s /q c:\\",
    "rmdir /s /q c:\\",
    "diskpart",
    "cipher /w:",
    "fsutil file setzerodata",
    "reg delete hklm",
    "bcdedit",
]

# Sensitive / destructive commands that require explicit user confirmation (confirm=True)
CONFIRM_REQUIRED_PREFIXES = (
    "del ", "erase ", "rd ", "rmdir ",
    "remove-item ", "rm ", "ri ",
    "shutdown", "restart-computer", "stop-computer", "logoff",
    "stop-process", "taskkill",
)


def shell_run(command: str, timeout: int = 30, confirmed: bool = False) -> str:
    if not command:
        return "No command specified."

    cmd_lower = command.lower().strip()

    for blocked in HARD_BLOCKED:
        if blocked in cmd_lower:
            return f"Security block: System-critical destructive command is not allowed ({blocked})."

    if not confirmed and any(cmd_lower.startswith(p) or f" {p}" in cmd_lower for p in CONFIRM_REQUIRED_PREFIXES):
        return (
            f"CONFIRMATION REQUIRED: The command '{command}' is destructive or sensitive. "
            "Ask the user for verbal confirmation first. Once the user says yes/confirm, "
            "call shell_run again with confirmed=True."
        )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        if not output:
            return "Command executed successfully (no output)."
        if len(output) > 1000:
            output = output[:1000] + "\n... (output truncated)"
        return output
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing command: {e}"
