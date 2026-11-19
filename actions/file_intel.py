"""
File Intelligence for R.O.S.E.
- Searches the user's computer (Desktop, Downloads, Documents, or custom path) for files
- Reads PDFs (.pdf), Word documents (.docx), Excel spreadsheets (.xlsx), CSV, TXT, and source code
- Compares multiple files
- Organizes files into folders (with confirmation gate)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


HOME = Path.home()
SEARCH_ROOTS = {
    "desktop": HOME / "Desktop",
    "downloads": HOME / "Downloads",
    "documents": HOME / "Documents",
    "pictures": HOME / "Pictures",
    "home": HOME,
}


def _resolve_folder(folder_hint: str) -> Path:
    raw = (folder_hint or "home").strip()
    low = raw.lower()
    if low in SEARCH_ROOTS:
        return SEARCH_ROOTS[low]
    p = Path(os.path.expandvars(raw)).expanduser()
    if p.exists():
        return p
    for key, path in SEARCH_ROOTS.items():
        if key in low:
            return path
    return HOME


def _read_single_file(file_path: Path, max_chars: int = 4000) -> str:
    if not file_path.exists() or not file_path.is_file():
        return f"File not found: {file_path}"

    suffix = file_path.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader  # type: ignore
            reader = PdfReader(str(file_path))
            pages_text = []
            for i, page in enumerate(reader.pages[:15], start=1):
                txt = page.extract_text() or ""
                if txt.strip():
                    pages_text.append(f"[Page {i}] {txt.strip()}")
            joined = "\n".join(pages_text)
            return f"[PDF: {file_path.name} ({len(reader.pages)} pages)]\n{joined[:max_chars]}"

        elif suffix == ".docx":
            import docx  # type: ignore
            import zipfile
            import xml.etree.ElementTree as ET

            doc = docx.Document(str(file_path))
            blocks = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            for tbl in doc.tables:
                for row in tbl.rows:
                    row_txt = " | ".join(
                        cell.text.strip() for cell in row.cells if cell.text.strip()
                    )
                    if row_txt and row_txt not in blocks:
                        blocks.append(row_txt)
            if len("\n".join(blocks)) < 80:
                try:
                    with zipfile.ZipFile(file_path) as zf:
                        xml_data = zf.read("word/document.xml")
                    xml_root = ET.fromstring(xml_data)
                    xml_lines: list[str] = []
                    for p_el in xml_root.iter():
                        if p_el.tag.endswith("}p"):
                            txt = "".join(
                                t.text for t in p_el.iter() if t.tag.endswith("}t") and t.text
                            ).strip()
                            if txt:
                                xml_lines.append(txt)
                    if xml_lines:
                        blocks = xml_lines
                except Exception:
                    pass
            joined = "\n".join(blocks)
            return f"[WORD DOCUMENT: {file_path.name}]\n{joined[:max_chars]}"

        elif suffix in (".xlsx", ".xlsm"):
            import openpyxl  # type: ignore
            wb = openpyxl.load_workbook(str(file_path), data_only=True, read_only=True)
            out = [f"[EXCEL SPREADSHEET: {file_path.name} | Sheets: {', '.join(wb.sheetnames)}]"]
            for sheet_name in wb.sheetnames[:3]:
                ws = wb[sheet_name]
                out.append(f"--- Sheet: {sheet_name} ---")
                for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
                    if row_idx >= 35:
                        out.append("... (more rows omitted)")
                        break
                    if any(cell is not None for cell in row):
                        out.append(" | ".join(str(c) if c is not None else "" for c in row[:12]))
            wb.close()
            return "\n".join(out)[:max_chars]

        else:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            return f"[FILE: {file_path.name}]\n{text[:max_chars]}"
    except Exception as exc:
        return f"Error reading {file_path.name}: {exc}"


def file_intelligence(
    action: str,
    query_or_path: str = "",
    second_path: str = "",
    folder: str = "home",
    confirmed: bool = False,
) -> str:
    """
    - action='search': Searches for files matching query_or_path in Desktop, Downloads, Documents, or specified folder.
    - action='read': Reads and extracts content from a PDF, Word (.docx), Excel (.xlsx), TXT, CSV, or code file.
    - action='compare': Reads and compares two files (query_or_path and second_path).
    - action='organize': Organizes files in a folder by category (Documents, Images, Videos, Audio, Installers) — requires confirmed=True.
    - action='create_folder': Creates a new folder (query_or_path is folder name or full path; folder sets parent directory like 'desktop', 'documents', 'downloads').
    """
    act = (action or "search").strip().lower()

    if act in ("create_folder", "mkdir", "new_folder", "make_folder", "create_directory"):
        raw = (query_or_path or "").strip()
        if not raw:
            return "Please specify the name or path of the folder you want to create."
        # Strip accidental surrounding quotes
        raw = raw.strip("\"'")
        expanded = Path(os.path.expandvars(raw)).expanduser()
        if expanded.is_absolute():
            target = expanded
        else:
            parent_hint = (folder or "").strip().lower()
            # Default to Desktop when user says e.g. "create a folder called Work" without specifying where, unless parent given
            if not parent_hint or parent_hint == "home":
                # Check if raw itself starts with a known root like "downloads/xyz" or "documents/xyz"
                parts = Path(raw).parts
                if parts and parts[0].lower() in SEARCH_ROOTS and parts[0].lower() != "home":
                    base = SEARCH_ROOTS[parts[0].lower()]
                    target = base.joinpath(*parts[1:]) if len(parts) > 1 else base
                else:
                    target = SEARCH_ROOTS["desktop"] / raw
            else:
                base = _resolve_folder(folder)
                target = base / raw
        try:
            already_existed = target.exists() and target.is_dir()
            target.mkdir(parents=True, exist_ok=True)
            if already_existed:
                return f"Folder '{target.name}' already exists at {target}."
            return f"Created folder '{target.name}' at {target}."
        except Exception as exc:
            return f"Could not create folder '{raw}': {exc}"

    if act == "search":
        needle = (query_or_path or "").strip().lower()
        root = _resolve_folder(folder)
        matches = []
        search_dirs = [
            SEARCH_ROOTS["desktop"],
            SEARCH_ROOTS["downloads"],
            SEARCH_ROOTS["documents"],
        ] if root == HOME else [root]

        for d in search_dirs:
            if not d.exists():
                continue
            try:
                for p in d.rglob("*"):
                    if p.is_file() and (not needle or needle in p.name.lower()):
                        matches.append(p)
                        if len(matches) >= 20:
                            break
            except Exception:
                continue
            if len(matches) >= 20:
                break

        if not matches:
            return f"No files matching '{query_or_path}' found in {', '.join(str(d) for d in search_dirs)}."
        lines = [f"Found {len(matches)} file(s):"]
        for m in matches:
            size_kb = m.stat().st_size // 1024
            lines.append(f"- {m.name} ({size_kb} KB) -> {m}")
        return "\n".join(lines)

    elif act == "read":
        raw = (query_or_path or "").strip()
        if not raw:
            return "Please specify the file name or path to read."
        p = Path(os.path.expandvars(raw)).expanduser()
        if not p.exists():
            # Search for it automatically in Desktop, Downloads, Documents
            for d in (SEARCH_ROOTS["desktop"], SEARCH_ROOTS["downloads"], SEARCH_ROOTS["documents"]):
                if not d.exists():
                    continue
                found = [f for f in d.rglob("*") if f.is_file() and raw.lower() in f.name.lower()]
                if found:
                    p = found[0]
                    break
        return _read_single_file(p)

    elif act == "compare":
        p1 = Path(os.path.expandvars(query_or_path)).expanduser()
        p2 = Path(os.path.expandvars(second_path)).expanduser()
        c1 = _read_single_file(p1, max_chars=2200)
        c2 = _read_single_file(p2, max_chars=2200)
        return f"=== DOCUMENT 1 ===\n{c1}\n\n=== DOCUMENT 2 ===\n{c2}"

    elif act == "organize":
        target_dir = _resolve_folder(folder or query_or_path or "downloads")
        if not confirmed:
            return (
                f"CONFIRMATION REQUIRED: Organizing '{target_dir}' will move files into categorized subfolders "
                "(Documents, Images, Videos, Audio, Archives, Installers). Ask the user to confirm first, "
                "then call file_intelligence(action='organize', folder=..., confirmed=True)."
            )
        categories = {
            "Documents": {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".txt", ".csv"},
            "Images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"},
            "Videos": {".mp4", ".mkv", ".avi", ".mov"},
            "Audio": {".mp3", ".wav", ".m4a", ".flac"},
            "Installers": {".exe", ".msi"},
            "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
        }
        moved = 0
        for item in list(target_dir.iterdir()):
            if not item.is_file():
                continue
            ext = item.suffix.lower()
            for cat_name, exts in categories.items():
                if ext in exts:
                    dest_folder = target_dir / cat_name
                    dest_folder.mkdir(exist_ok=True)
                    shutil.move(str(item), str(dest_folder / item.name))
                    moved += 1
                    break
        return f"Organized {moved} files into category folders inside {target_dir}."

    return f"Unknown file_intelligence action: {action}"

