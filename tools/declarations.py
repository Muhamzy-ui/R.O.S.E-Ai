"""
R.O.S.E (Project Mirror) - Tool Declarations and Schemas
Extracted for central registry and Gemini Live API integration.
"""

from pathlib import Path

from app_config import get_app_config_value

PROMPT_PATH = Path(__file__).resolve().parent.parent / "core" / "prompt.txt"

PROTECTED_PERSONAL_TOOLS = {
    "get_memory",
    "delete_memory",
    "get_calendar_events",
    "add_calendar_event",
    "delete_calendar_event",
    "get_reminders",
    "add_reminder",
    "file_intelligence",
    "send_whatsapp_message",
    "save_whatsapp_contact",
    "get_whatsapp_unread",
    "swiftsats_intelligence",
    "business_advisor",
    "shell_run",
    "job_assistant",
    "job_command_center",
}



TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens or brings into focus ANY installed application on Windows (Firefox, Chrome, Tor Browser, Antigravity IDE, WhatsApp, "
            "Telegram, Microsoft Teams, Microsoft Store, Steam, Epic Games Launcher, EA, TikTok LIVE Studio, VLC, 7-Zip, Git Bash, Nmap, "
            "Edge, Terminal, Explorer, VS Code, Notepad, Calculator, Camera, Settings, etc.) by searching Start Menu apps, Registry App Paths, "
            "and shortcuts with fuzzy matching. Pass app_name='list' (or 'list:<keyword>') to list installed apps. "
            "Restores an already-open window automatically unless force_new=True."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Application name (e.g. 'Firefox', 'Chrome', 'Tor Browser', 'Antigravity', 'WhatsApp', 'Teams', 'Steam', 'list')"
                },
                "force_new": {
                    "type": "BOOLEAN",
                    "description": "Set True ONLY if the user explicitly asks for a new window or new instance."
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "open_workspace",
        "description": (
            "Opens or restores the user's daily workspace suite (Chrome, Antigravity, and WhatsApp by default). "
            "Checks if each app is already open and brings existing windows forward instead of opening duplicates, "
            "unless force_new=True is explicitly requested."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "apps": {
                    "type": "STRING",
                    "description": "Optional comma-separated list of apps (defaults to 'Chrome, Antigravity, WhatsApp')"
                },
                "force_new": {
                    "type": "BOOLEAN",
                    "description": "Set True ONLY when the user says 'open new' / 'open fresh windows'."
                }
            }
        }
    },
    {
        "name": "install_or_download",
        "description": (
            "Finds and opens official download links ('get_download_link'), opens Microsoft Store to an app ('microsoft_store'), "
            "downloads and installs any Windows application ('install_app' — e.g. Spotify, YMusic / YouTube Music, VLC, Chrome, Brave, WhatsApp, Telegram, VS Code, Discord, CapCut, Steam, AnyDesk, etc.), "
            "or downloads any song/music track ('download_music'), video ('download_video'), or file ('download_file') into the user's Downloads folder."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "install_app | get_download_link | microsoft_store | download_music | download_video | download_file | open_downloads"
                },
                "target": {
                    "type": "STRING",
                    "description": "App/software name (e.g. 'Spotify', 'YMusic', 'VLC', 'Blender') or song/video search query (e.g. 'Burna Boy Last Last')"
                },
                "url": {
                    "type": "STRING",
                    "description": "Optional direct URL to download"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "sys_info",
        "description": "Gets Windows system information: battery status, CPU, RAM, disk, time, date, network connectivity.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "battery | cpu | ram | disk | time | date | network | all"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_weather",
        "description": "Gets a real-time weather summary for a city or location (defaults to your current active location).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "location": {
                    "type": "STRING",
                    "description": "City or location name (e.g. 'Lagos', 'Abuja', 'London')."
                }
            }
        }
    },
    {
        "name": "set_location",
        "description": "Changes R.O.S.E's active city/location, timezone, live clock panel, weather panel, and morning briefings (e.g. 'London', 'Abuja', 'New York', 'Dubai', 'Lagos').",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "location": {
                    "type": "STRING",
                    "description": "New city or location name (e.g. 'London', 'Abuja', 'New York', 'Tokyo', 'Lagos')"
                }
            },
            "required": ["location"]
        }
    },
    {
        "name": "get_calendar_events",
        "description": "Reads the local Windows calendar and summarizes today's, tomorrow's, next, or upcoming events.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "today | tomorrow | next | agenda | week"
                },
                "limit": {
                    "type": "NUMBER",
                    "description": "Maximum number of events"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "add_calendar_event",
        "description": "Adds a new event to the Windows calendar.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "Event title"},
                "start_iso": {"type": "STRING", "description": "Start date/time in ISO or yyyy-MM-dd HH:mm format."},
                "end_iso": {"type": "STRING", "description": "End date/time (optional)."},
                "location": {"type": "STRING", "description": "Event location (optional)."},
                "notes": {"type": "STRING", "description": "Event notes (optional)."},
                "calendar_name": {"type": "STRING", "description": "Calendar name (optional)."},
                "all_day": {"type": "BOOLEAN", "description": "True if all-day event."}
            },
            "required": ["title", "start_iso"]
        }
    },
    {
        "name": "delete_calendar_event",
        "description": "Deletes an event from the Windows calendar.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "Title of the event to delete"},
                "start_iso": {"type": "STRING", "description": "Optional start date/time to disambiguate."},
                "calendar_name": {"type": "STRING", "description": "Optional calendar name"},
                "delete_all_matches": {"type": "BOOLEAN", "description": "If true, deletes all matching events"}
            },
            "required": ["title"]
        }
    },
    {
        "name": "get_reminders",
        "description": "Reads reminders, checks exact remaining minutes/seconds countdown ('remaining'), or clears old overdue alerts ('clear_overdue').",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "remaining | upcoming | today | overdue | next | all | clear_overdue"},
                "limit": {"type": "NUMBER", "description": "Maximum number of reminders"},
                "list_name": {"type": "STRING", "description": "Optional reminder list name"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "add_reminder",
        "description": (
            "Adds a timed reminder or scheduled task that alerts out loud when due AND optionally executes a PC action "
            "automatically via scheduled_action (e.g. 'open_app:Chrome', 'play_media:Burna Boy', 'open_workspace', "
            "'minimize_app:notepad', 'lock_screen', 'sleep_laptop', 'web_research:AI trends')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "Reminder title"},
                "due_iso": {
                    "type": "STRING",
                    "description": "Due time: relative ('in 2 minutes', 'in 30 seconds', 'in 1 hour'), 'HH:MM' ('14:30'), or ISO ('2026-11-12T09:00')"
                },
                "notes": {"type": "STRING", "description": "Optional notes"},
                "list_name": {"type": "STRING", "description": "Optional list name"},
                "priority": {"type": "STRING", "description": "low | medium | high"},
                "all_day": {"type": "BOOLEAN", "description": "True if all-day reminder"},
                "scheduled_action": {
                    "type": "STRING",
                    "description": "Optional PC action to execute automatically when due: 'open_app:<app>', 'open_workspace', 'play_media:<song>', 'minimize_app:<app>', 'lock_screen', 'sleep_laptop', 'web_research:<topic>'"
                }
            },
            "required": ["title"]
        }
    },
    {
        "name": "browser_control",
        "description": "Opens a URL in Chrome, searches Google, plays YouTube automatically, or clicks Play on paused music/video in Chrome ('click_play').",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "open_url | search | play_youtube | click_play"},
                "url":    {"type": "STRING", "description": "URL to open (for open_url)"},
                "query":  {"type": "STRING", "description": "Search query (for search or play_youtube)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "web_research",
        "description": (
            "Opens R.O.S.E's interactive Live Visual Search ('VR Search') HUD showing live step-by-step web searching, "
            "a clear plain-English summary, verified per-item photography, and a local map. "
            "Use this for restaurants/food/breakfast spots ('where can I eat this morning', 'restaurants near me'), "
            "romantic/girlfriend date spots and hangouts, products/prices/comparisons, news, companies, or deep topics — "
            "as well as when the user says 'VR search', 'VR research', 'open research', or 'research mode'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "What to search for: restaurants, cafes, breakfast/date spots, places near me, "
                        "products, comparisons, companies, or research questions"
                    ),
                },
                "mode": {"type": "STRING", "description": "deep_search | places | read_url | close"},
                "url": {"type": "STRING", "description": "Specific webpage URL when mode is 'read_url'"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "file_intelligence",
        "description": (
            "Searches the user's computer for files ('search'), reads PDFs/Word/Excel/code ('read'), compares two documents ('compare'), "
            "creates a new folder ('create_folder'), or organizes a folder into categories ('organize')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "search | read | compare | create_folder | organize"},
                "query_or_path": {"type": "STRING", "description": "File/folder name, search keyword, or full path"},
                "second_path": {"type": "STRING", "description": "Second file path when action is 'compare'"},
                "folder": {"type": "STRING", "description": "desktop | downloads | documents | pictures | home"},
                "confirmed": {"type": "BOOLEAN", "description": "Set True only after user verbally confirms organizing files"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "shell_run",
        "description": "Executes a Windows PowerShell command for file operations or system management. Requires confirmed=True for sensitive/delete commands.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {"type": "STRING", "description": "PowerShell command to execute"},
                "confirmed": {"type": "BOOLEAN", "description": "True if the user has verbally confirmed a destructive/sensitive action"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "play_media",
        "description": "Plays a song, music, or video on YouTube, Spotify Desktop, YMusic (YouTube Music), or Apple Music Web.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Song, artist, album, or video search query"},
                "provider": {"type": "STRING", "description": "auto | youtube | spotify | ymusic | apple_music"},
                "autoplay": {"type": "BOOLEAN", "description": "If true, plays directly"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_youtube_channel_report",
        "description": "Reports public statistics and recent video performance for a YouTube channel.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Analysis request in natural language"},
                "handle": {"type": "STRING", "description": "Optional channel handle or URL"},
                "video_limit": {"type": "NUMBER", "description": "Number of recent videos to include (default 6)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "analyze_screen",
        "description": (
            "Captures screenshots across ALL connected monitors ('all_monitors', default), a single display "
            "('monitor_1', 'monitor_2', 'monitor_3'), or just the foreground window ('active_window'), and reads/analyzes everything using AI Vision."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "User's question about what is on the screen(s) (e.g. 'Read both monitors', 'What jobs are on screen?')"},
                "target": {"type": "STRING", "description": "all_monitors | monitor_1 | monitor_2 | monitor_3 | active_window"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "save_memory",
        "description": "Silently saves important facts, items, notes, passwords, or preferences the user asks ROSE to keep/remember.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {"type": "STRING", "description": "identity | preferences | projects | notes"},
                "key":   {"type": "STRING", "description": "Short key (e.g. 'name', 'exam_date', 'secret_note')"},
                "value": {"type": "STRING", "description": "Value to store in English"}
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "delete_memory",
        "description": "Deletes a record from persistent memory when the user asks to forget or remove something.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {"type": "STRING", "description": "notes | identity | preferences | projects"},
                "key": {"type": "STRING", "description": "Key to delete"},
                "match_text": {"type": "STRING", "description": "Natural language phrase to match and delete"}
            }
        }
    },
    {
        "name": "send_whatsapp_message",
        "description": "Sends a real verified message via WhatsApp with server acknowledgment (supports contact name matching and Nigerian 080/081/090/+234 numbers). Requires user confirmation (Tier 2).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "recipient_name": {"type": "STRING", "description": "Contact name (e.g. 'Aisha', 'Mom')"},
                "phone_number": {"type": "STRING", "description": "Phone number (e.g. 0803... or +234...)"},
                "message": {"type": "STRING", "description": "Message content to send"},
                "confirmed": {"type": "BOOLEAN", "description": "Set True only after user verbally confirms sending"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "get_whatsapp_unread",
        "description": "Reads unread WhatsApp messages and returns a clean spoken/written summary of recent unread chats (e.g. 'any WhatsApp messages?').",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "save_whatsapp_contact",
        "description": "Saves a frequently used WhatsApp contact with name and phone number.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "display_name": {"type": "STRING", "description": "Contact name"},
                "phone_number": {"type": "STRING", "description": "Phone number"},
                "aliases": {"type": "STRING", "description": "Comma-separated aliases"}
            },
            "required": ["display_name", "phone_number"]
        }
    },
    {
        "name": "rose_window_control",
        "description": "Controls ROSE's own window on the screen: opens/shows the full screen, minimizes/hides the screen, or switches to the floating mini orb.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "show | minimize | mini_orb"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "laptop_control",
        "description": (
            "Controls any hardware, window, power, or UI action on the Windows laptop: "
            "minimize_app, maximize_app, restore_app, close_app, list_open_windows (for any single app like 'notepad', 'chrome', 'edge', 'spotify', 'antigravity'), "
            "snap_window (snaps target app to 'left', 'right', 'top', 'bottom', 'top_left', 'top_right', 'bottom_left', 'bottom_right', or 'center'), "
            "split_screen (tiles target=left_app and value=right_app side by side), "
            "volume_up, volume_down, volume_mute, set_volume (0-100), set_brightness (0-100), "
            "type_text (types into the active window), press_key/hotkey (e.g. 'ctrl+c', 'alt+tab', 'enter', 'win+d'), "
            "scroll_up, scroll_down, media_play_pause, media_next, media_prev, screenshot, "
            "close_active_window, minimize_all, lock_screen, sleep_laptop (standby/sleep), "
            "shutdown_self, sleep_and_shutdown_self, restart_laptop, hibernate, shutdown_laptop."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "minimize_app | maximize_app | restore_app | close_app | list_open_windows | "
                        "snap_window | split_screen | snap_left | snap_right | "
                        "volume_up | volume_down | volume_mute | set_volume | set_brightness | "
                        "type_text | press_key | hotkey | scroll_up | scroll_down | "
                        "media_play_pause | media_next | media_prev | screenshot | "
                        "close_active_window | minimize_all | lock_screen | sleep_laptop | "
                        "shutdown_self | sleep_and_shutdown_self | restart_laptop | hibernate | shutdown_laptop"
                    )
                },
                "target": {"type": "STRING", "description": "App name for window actions/snapping, or left-hand app for split_screen (e.g. 'Antigravity')"},
                "value": {"type": "STRING", "description": "Snap position ('left'|'right'|'center'), right-hand app for split_screen ('Chrome'), numeric level ('0'-'100'), or hotkey"},
                "text": {"type": "STRING", "description": "Text to type when action is 'type_text'"},
                "confirmed": {"type": "BOOLEAN", "description": "Set True ONLY after user explicitly confirms power actions like sleep_laptop, hibernate, or shutdown_laptop"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "voice_security",
        "description": (
            "Stores, verifies, checks, or clears the owner's ('Boss') biometric voiceprint. "
            "When the user asks ROSE to store/learn/save their voice ('enroll' / 'add_sample'), "
            "or before sharing personal info when voice lock is active ('verify'), call this tool."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "enroll | add_sample | verify | status | clear"
                },
                "owner_name": {
                    "type": "STRING",
                    "description": "Optional name for the owner (default 'Boss')"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "camera_vision",
        "description": (
            "Identifies what the user is holding via a high-resolution sharp camera still ('identify', e.g. 'what am I holding?' or 'what is this?'), "
            "re-analyzes with previous context when shown the back/label/barcode ('reanalyze'), "
            "saves the current item as a named visual reference in camera_memory ('remember', with query='<name>'), "
            "lists saved camera items ('list_saved'), or controls the on-demand live view / Security Guard window "
            "('open', 'filter', 'guard_on', 'guard_off', 'connect_cctv', 'close')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "identify | reanalyze | remember | list_saved | open | filter | guard_on | guard_off | connect_cctv | close"
                },
                "query": {
                    "type": "STRING",
                    "description": "Question for 'identify'/'reanalyze', item name for 'remember', object filter for 'filter', or RTSP/HTTP URL for 'connect_cctv'"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "get_memory",
        "description": "Retrieves all facts, notes, passwords, or items the user previously asked ROSE to keep/store in memory.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "morning_wakeup_control",
        "description": (
            "Delivers the executive morning greeting & today's plan ('briefing' — call this whenever the user says 'Good morning'!) "
            "or configures/tests the daily 7:00 AM Personal Morning Wake-Up Assistant ('enable', 'disable', 'status', 'test_now')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "briefing | enable | disable | status | test_now"},
                "time_hhmm": {"type": "STRING", "description": "Wake-up time in 24h HH:MM format (default '07:00')"},
                "phone_number": {"type": "STRING", "description": "Optional Nigerian (+234) mobile number for Twilio wake-up call"},
                "call_enabled": {"type": "BOOLEAN", "description": "True to enable outbound phone call via Twilio if configured"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "swiftsats_intelligence",
        "description": (
            "Queries authorised read-only SwiftSats business & health metrics (signups_per_day, active_users, "
            "logins_per_day, failed_logins, transactions_count_and_volume, failed_or_stuck_payments, error_rate, "
            "service_uptime), runs security_check (headers, TLS, failed-login spike), or generates 24h/7d/30d "
            "markdown reports saved in reports/. Never fakes metrics when disabled."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "daily_report | logins today | 7d | 30d | 24h | security_check | signups | active_users | transactions | anomalies"
                },
                "window": {
                    "type": "STRING",
                    "description": "Optional time window override: 24h | 7d | 30d"
                },
                "repo_path": {
                    "type": "STRING",
                    "description": "Optional local SwiftSats repo path ONLY if user requests dependency audit"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "business_advisor",
        "description": (
            "Activates candid, analytical Business Advisor Mode grounded in the founder's business_brain/*.md venture files "
            "(SwiftSats, NearEat, BountyTrust, Autonomous Governance Engine, Petroleum Supply Chain system, M.B.O WebDev, me_and_goals.md). "
            "Challenges weak assumptions, highlights risks and missing numbers, asks sharp questions, and proposes next steps."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "The strategic question, decision, or review topic from the user"
                },
                "venture": {
                    "type": "STRING",
                    "description": "Optional focus venture (SwiftSats, NearEat, BountyTrust, Autonomous Governance Engine, Petroleum Supply Chain, M.B.O WebDev)"
                },
                "channel": {
                    "type": "STRING",
                    "description": "voice | chat | telegram (default 'voice')"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "busy_bar_control",
        "description": (
            "Controls the 72x16 LED Dot-Matrix 'Busy Bar' desk display (like Huw Prosser's Busy Bar TikTok) on screen "
            "AND pushes to a physical Flipper Busy Bar over HTTP API (/api/display/draw) if connected. "
            "Can display custom status text ('Hello, World!', 'DO NOT DISTURB', 'STUDYING') or a live countdown timer."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "display | timer | close"},
                "text": {"type": "STRING", "description": "Text to display on the 72x16 LED matrix (e.g. 'Hello, World!', 'BUSY CODING')"},
                "countdown_minutes": {"type": "NUMBER", "description": "Optional countdown minutes for Pomodoro/focus timer"},
                "device_ip": {"type": "STRING", "description": "Optional IP address of physical Busy Bar (default '10.0.4.20')"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "job_assistant",
        "description": (
            "Finds high-paying AI training/coding task websites for Nigeria ('ai_task_platforms'), searches live remote jobs ('search_jobs'), "
            "queues up to 20 daily roles tailored to the user's CV ('daily_sprint'), opens batches of up to 5 tabs in Chrome with clipboard-ready "
            "cover letters for fast user review & submission ('open_batch'), writes follow-up/async video scripts ('video_script') and follow-up emails ('follow_up'), "
            "manages CV profile ('save_profile'/'get_profile'), prepares single applications ('prepare_application'/'apply_now'), and tracks daily count ('list_applications')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "ai_task_platforms | search_jobs | daily_sprint | open_batch | video_script | follow_up | save_profile | get_profile | prepare_application | apply_now | list_applications"
                },
                "query": {"type": "STRING", "description": "Job role/keywords (e.g. 'Python Django', 'AI trainer') or video pitch topic"},
                "location": {"type": "STRING", "description": "Preferred job location (e.g. 'Remote', 'Nigeria', 'Worldwide')"},
                "resume_path": {"type": "STRING", "description": "Optional path to CV/resume file"},
                "full_name": {"type": "STRING", "description": "Candidate full name"},
                "email": {"type": "STRING", "description": "Candidate email"},
                "phone": {"type": "STRING", "description": "Candidate phone"},
                "skills": {"type": "STRING", "description": "Candidate key skills"},
                "url": {"type": "STRING", "description": "Optional direct job application URL"},
                "job_index": {"type": "NUMBER", "description": "1-based index from the last search_jobs results"},
                "confirmed": {"type": "BOOLEAN", "description": "Set True to open application tabs/batch pages in Chrome for review & submit"},
                "batch_size": {"type": "NUMBER", "description": "Number of tabs to open per batch (1-5, default 5)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "pc_optimizer",
        "description": (
            "Monitors laptop speed and optimizes memory/storage: reports top RAM/CPU hogs ('health_report'), "
            "safely frees idle RAM across background processes without closing apps ('trim_ram'), "
            "closes a specific heavy non-system app ('close_heavy_app', requires confirmed=True), "
            "cleans old Windows %TEMP% files ('clean_temp', requires confirmed=True), or tests internet latency ('network_check')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "health_report | top_apps | trim_ram | close_heavy_app | clean_temp | network_check"},
                "target": {"type": "STRING", "description": "Heavy app name to close (e.g. 'chrome', 'teams')"},
                "confirmed": {"type": "BOOLEAN", "description": "Set True only after user confirms closing an app or cleaning Temp files"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "clipboard_copilot",
        "description": (
            "Instant AI assistant for whatever the user just copied to their Windows clipboard: reads clipboard ('read'), "
            "drafts a polished professional reply ready to Ctrl+V ('reply'), summarizes long copied text ('summarize'), "
            "debugs/fixes a copied error or code snippet ('fix_code'), rewrites/polishes writing ('rewrite'), or translates ('translate')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "read | reply | summarize | fix_code | rewrite | translate | history"},
                "instruction": {"type": "STRING", "description": "Specific instruction/tone/target language (e.g. 'decline politely', 'make concise', 'Yoruba')"},
                "copy_back": {"type": "BOOLEAN", "description": "Copies generated output back to clipboard automatically (default True)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "document_creator",
        "description": (
            "Generates clean office documents and coding starters directly on disk: Word documents ('create_word_doc'), "
            "text-searchable PDFs ('create_pdf'), Excel spreadsheets ('create_excel'), client invoices in PDF+Word ('create_invoice'), "
            "or starter coding projects with git init ('scaffold_project')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "create_word_doc | create_pdf | create_excel | create_invoice | scaffold_project"},
                "title": {"type": "STRING", "description": "Document title, invoice title, or project name"},
                "content": {"type": "STRING", "description": "Body text, topic/prompt for AI drafting, or notes"},
                "folder": {"type": "STRING", "description": "desktop | documents | downloads | or folder path"},
                "client_name": {"type": "STRING", "description": "Client name when creating an invoice"},
                "items_or_rows": {"type": "STRING", "description": "Invoice items ('Design:50000, Hosting:15000') or Excel rows ('Item,Cost; Domain,12000')"},
                "currency": {"type": "STRING", "description": "NGN | USD | GBP | EUR"},
                "project_type": {"type": "STRING", "description": "python | fastapi | web (for scaffold_project)"},
                "open_after": {"type": "BOOLEAN", "description": "Open the created file/project right away"}
            },
            "required": ["action", "title"]
        }
    },
    {
        "name": "content_digest",
        "description": (
            "Distills long content quickly: summarizes any YouTube video/lecture by URL or search query ('summarize_youtube'), "
            "summarizes any web article URL ('summarize_article'), extracts concrete to-do action items from text or clipboard ('extract_tasks'), "
            "or performs live currency conversion such as USD/GBP/EUR to Naira ('currency_convert')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "summarize_youtube | summarize_article | extract_tasks | currency_convert"},
                "target": {"type": "STRING", "description": "YouTube link/title, article URL, or text/notes"},
                "amount": {"type": "NUMBER", "description": "Amount to convert for currency_convert"},
                "from_currency": {"type": "STRING", "description": "Source currency code (e.g. 'USD', 'GBP', 'NGN')"},
                "to_currency": {"type": "STRING", "description": "Target currency code (e.g. 'NGN', 'USD')"},
                "save_note": {"type": "BOOLEAN", "description": "Save summary as a Markdown note"},
                "create_reminders": {"type": "BOOLEAN", "description": "Automatically add extracted action items to reminders"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "workflow_autopilot",
        "description": (
            "Executes multi-step productivity routines and developer Git/testing workflows: "
            "Routines: 'deep_work' (workspace + Antigravity/Chrome split-screen + Busy Bar timer), 'morning_startup', 'end_of_day', 'job_hunt_sprint', 'meeting_mode', 'focus_stats'. "
            "Developer Co-Pilot: 'git_status', 'git_diff', 'git_commit' (requires confirmed=True), 'run_tests', 'explain_error'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "deep_work | morning_startup | end_of_day | job_hunt_sprint | meeting_mode | focus_stats | git_status | git_diff | git_commit | run_tests | explain_error"
                },
                "target": {"type": "STRING", "description": "Optional project path/name, job role for job_hunt_sprint, or focus label"},
                "message": {"type": "STRING", "description": "Git commit message or error context"},
                "minutes": {"type": "NUMBER", "description": "Focus timer minutes for deep_work (default 45)"},
                "confirmed": {"type": "BOOLEAN", "description": "Set True after user confirms git_commit"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "interview_mode_control",
        "description": (
            "Switches R.O.S.E into or out of 100% SILENT Interview Mode. "
            "When active ('start'), R.O.S.E mutes all spoken audio & sound effects, listens directly to the laptop's "
            "system speaker audio (WASAPI Loopback — Meet/Zoom/Teams/Chrome), and streams concise written answers "
            "grounded in the user's CV onto the floating Interview Teleprompter HUD. "
            "Use 'stop' to exit back to normal voice mode, 'ask' to silently answer a typed/clipboard question, "
            "or 'screen' to read & solve an on-screen coding/quiz problem silently."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "start | stop | toggle | ask | screen | mic_on | mic_off | status"},
                "role": {"type": "STRING", "description": "Optional target job role (e.g. 'Cybersecurity Analyst', 'Python Developer')"},
                "company": {"type": "STRING", "description": "Optional company name"},
                "question": {"type": "STRING", "description": "Optional typed question or 'clipboard' when action is 'ask'"},
                "also_mic": {"type": "BOOLEAN", "description": "True only if user explicitly wants mic listened alongside speaker loopback"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "job_command_center",
        "description": (
            "Opens, scans, or checks the R.O.S.E Job Command Center & Application Tracker (PySide6). "
            "Scans official remote job feeds (RemoteOK, WeWorkRemotely RSS, Hacker News Who Is Hiring, Himalayas, Jobspresso, "
            "plus Reddit only when PRAW credentials are configured in .env), scores and ranks jobs 0-100 against Mahmud's "
            "jobs_profile.yaml (Nigeria WAT eligibility, weighted skills, blacklist filtering), tracks submitted applications, "
            "alerts for 4-day follow-ups, reports source health, and drafts CV-grounded pitches."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "open (default) | scan | top_picks | health | analytics | autofill | search_chrome"},
                "query": {"type": "STRING", "description": "Optional search keyword (e.g. 'python django', 'react', 'scraping')"},
                "tier": {"type": "STRING", "description": "Optional filter: all (default) | full_time | contract | freelance | part_time"},
                "platform": {"type": "STRING", "description": "Optional platform for search_chrome: google | twitter | upwork"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "speak_on_laptop",
        "description": (
            "Speaks a statement, announcement, or answer out loud through the laptop speakers and shows it in the R.O.S.E. HUD conversation panel. "
            "Use whenever the user on Telegram (or remotely) asks R.O.S.E. to say, speak, read aloud, or announce something on the laptop."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {
                    "type": "STRING",
                    "description": "The exact polished English text or complete answer to speak out loud on the laptop speakers."
                }
            },
            "required": ["text"]
        }
    },
    {
        "name": "send_telegram_message",
        "description": (
            "Sends a message, answer, summary, report, or notification directly from the laptop to the owner's linked Telegram chat. "
            "Use whenever the user asks R.O.S.E. on the laptop to send, message, forward, or communicate something to them on Telegram."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "message": {
                    "type": "STRING",
                    "description": "Message text to send to the owner's Telegram chat."
                }
            },
            "required": ["message"]
        }
    }
]


def get_api_key() -> str:
    return str(get_app_config_value("gemini_api_key", "") or "")


def load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are ROSE (JARVIS) — a personal AI assistant running on Windows in Lagos, Nigeria. "
            "Always speak ONLY in English. You understand both Standard English and Nigerian Pidgin (Broken English). "
            "Always use your tools to complete tasks on the laptop."
        )


