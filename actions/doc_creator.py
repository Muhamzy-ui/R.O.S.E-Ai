"""
Document, Invoice, Spreadsheet & Starter Project Creator for R.O.S.E.
Uses already-installed python-docx, openpyxl, and a clean pure-Python text-searchable PDF generator.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import textwrap
from pathlib import Path

from actions.file_intel import SEARCH_ROOTS, _resolve_folder
from app_config import get_app_config_value


def _safe_filename(title: str, ext: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9._ -]+", "_", (title or "Document").strip()).strip(" .")
    stem = stem[:64] or "Document"
    if not stem.lower().endswith(ext.lower()):
        stem += ext
    return stem


def _target_dir(folder: str) -> Path:
    low = (folder or "desktop").strip().lower()
    if not low or low == "home":
        return SEARCH_ROOTS["desktop"]
    return _resolve_folder(folder)


def _draft_body_with_gemini(kind: str, title: str, content_or_topic: str) -> str:
    raw = (content_or_topic or "").strip()
    if len(raw) >= 180 and "\n" in raw:
        return raw
    api_key = str(get_app_config_value("gemini_api_key", "") or "").strip()
    if not api_key:
        return raw or f"{title}\n\nPrepared on {dt.date.today().isoformat()}."
    try:
        from app_config import generate_gemini_content
        prompt = (
            f"Write clean, well-structured {kind} content in professional English.\n"
            f"Title: {title}\n"
            f"User instructions/notes: {raw or title}\n"
            "Use clear section headings (with ## prefix) and bullet points (- prefix) where helpful. No markdown code fences."
        )
        resp = generate_gemini_content(api_key, contents=prompt)
        txt = (getattr(resp, "text", "") or "").strip()
        return txt or raw or title
    except Exception:
        return raw or title


def _write_clean_pdf(path: Path, title: str, body: str) -> None:
    """
    Pure-Python, zero-dependency, text-searchable multi-page PDF 1.4 writer (Letter/A4 portrait).
    """
    page_w, page_h = 595, 842  # A4
    margin_x, top_y, bottom_y = 52, 785, 56
    line_h = 15

    # Wrap all lines cleanly
    wrapped_lines: list[tuple[str, bool]] = [(title.strip(), True), ("", False)]
    for para in body.splitlines():
        p = para.strip()
        if not p:
            wrapped_lines.append(("", False))
            continue
        is_heading = p.startswith("#")
        clean_p = p.lstrip("#").strip()
        max_chars = 66 if is_heading else 84
        for wl in textwrap.wrap(clean_p, width=max_chars) or [""]:
            wrapped_lines.append((wl, is_heading))

    lines_per_page = (top_y - bottom_y) // line_h
    pages_data: list[list[tuple[str, bool]]] = [
        wrapped_lines[i : i + lines_per_page]
        for i in range(0, max(1, len(wrapped_lines)), lines_per_page)
    ]

    def _pdf_esc(s: str) -> str:
        safe = s.encode("latin-1", "replace").decode("latin-1")
        return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    objects: list[bytes] = []

    # 1: Catalog, 2: Pages, 3: Regular Font, 4: Bold Font
    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    objects.append(b"")  # placeholder for 2 0 obj (Pages)
    objects.append(b"3 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")
    objects.append(b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >> endobj\n")

    page_obj_ids: list[int] = []
    next_id = 5
    for page_lines in pages_data:
        stream_cmds = ["BT"]
        y = top_y
        for text_line, bold in page_lines:
            font_ref = "/F2 13" if bold else "/F1 10.5"
            stream_cmds.append(f"{font_ref} Tf 1 0 0 1 {margin_x} {y} Tm ({_pdf_esc(text_line)}) Tj")
            y -= line_h
        stream_cmds.append("ET")
        stream_bytes = "\n".join(stream_cmds).encode("latin-1")

        content_id = next_id
        page_id = next_id + 1
        next_id += 2
        page_obj_ids.append(page_id)

        objects.append(
            f"{content_id} 0 obj << /Length {len(stream_bytes)} >> stream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream endobj\n"
        )
        objects.append(
            f"{page_id} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >> endobj\n".encode("ascii")
        )

    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects[1] = f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {len(page_obj_ids)} >> endobj\n".encode("ascii")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


def _write_clean_docx(path: Path, title: str, body: str) -> None:
    import docx  # type: ignore

    doc = docx.Document()
    doc.add_heading(title.strip(), level=0)
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("##"):
            doc.add_heading(s.lstrip("#").strip(), level=2)
        elif s.startswith("#"):
            doc.add_heading(s.lstrip("#").strip(), level=1)
        elif s.startswith("- ") or s.startswith("* "):
            doc.add_paragraph(s[2:].strip(), style="List Bullet")
        else:
            doc.add_paragraph(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


def _parse_invoice_items(items_spec: str) -> list[tuple[str, float]]:
    parsed: list[tuple[str, float]] = []
    raw = (items_spec or "").strip()
    if not raw:
        return [("Professional Services", 0.0)]
    for part in re.split(r"[,;\n]+", raw):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            desc, val_s = part.rsplit(":", 1)
        elif "=" in part:
            desc, val_s = part.rsplit("=", 1)
        else:
            m = re.search(r"(\d+(?:\.\d+)?)\s*$", part)
            if m:
                desc = part[: m.start()].strip(" -:") or "Service"
                val_s = m.group(1)
            else:
                desc, val_s = part, "0"
        num_m = re.search(r"\d+(?:[.,]\d+)?", val_s.replace(",", ""))
        amount = float(num_m.group(0)) if num_m else 0.0
        parsed.append((desc.strip() or "Service", amount))
    return parsed or [("Professional Services", 0.0)]


def document_creator(
    action: str = "create_word_doc",
    title: str = "ROSE Document",
    content: str = "",
    folder: str = "desktop",
    client_name: str = "",
    items_or_rows: str = "",
    currency: str = "NGN",
    project_type: str = "python",
    open_after: bool = False,
) -> str:
    """
    Actions:
    - create_word_doc: Creates a styled .docx file (auto-drafts full text if only a topic is given)
    - create_pdf: Creates a clean text-searchable .pdf file
    - create_excel: Creates a formatted .xlsx spreadsheet (pass CSV/JSON/semicolon rows or topic)
    - create_invoice: Creates both .docx and .pdf client invoice with itemized totals
    - scaffold_project: Creates a starter coding project ('python', 'fastapi', 'flask', 'web', 'automation')
    """
    act = (action or "create_word_doc").strip().lower()
    out_dir = _target_dir(folder)
    out_dir.mkdir(parents=True, exist_ok=True)

    if act in ("create_word_doc", "word", "docx", "create_docx"):
        body = _draft_body_with_gemini("Word document", title, content)
        out_path = out_dir / _safe_filename(title, ".docx")
        _write_clean_docx(out_path, title, body)
        if open_after:
            try:
                import os
                os.startfile(str(out_path))
            except Exception:
                pass
        return f"Created Word document '{out_path.name}' at {out_path}."

    if act in ("create_pdf", "pdf"):
        body = _draft_body_with_gemini("PDF report", title, content)
        out_path = out_dir / _safe_filename(title, ".pdf")
        _write_clean_pdf(out_path, title, body)
        if open_after:
            try:
                import os
                os.startfile(str(out_path))
            except Exception:
                pass
        return f"Created PDF document '{out_path.name}' at {out_path}."

    if act in ("create_excel", "excel", "xlsx", "spreadsheet"):
        import openpyxl  # type: ignore
        from openpyxl.styles import Font  # type: ignore

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = re.sub(r"[\\/*?:\[\]]", "", title)[:28] or "Sheet1"

        rows_data: list[list[str]] = []
        raw_rows = (items_or_rows or content or "").strip()
        if raw_rows.startswith("["):
            try:
                parsed_json = json.loads(raw_rows)
                if isinstance(parsed_json, list) and parsed_json:
                    if isinstance(parsed_json[0], dict):
                        headers = list(parsed_json[0].keys())
                        rows_data.append(headers)
                        for obj in parsed_json:
                            rows_data.append([str(obj.get(h, "")) for h in headers])
                    elif isinstance(parsed_json[0], (list, tuple)):
                        rows_data = [[str(c) for c in r] for r in parsed_json]
            except Exception:
                rows_data = []

        if not rows_data and raw_rows:
            for line in re.split(r"[\n;]+", raw_rows):
                cells = [c.strip() for c in re.split(r"[,|]", line) if c.strip() or "," in line]
                if cells:
                    rows_data.append(cells)

        if not rows_data:
            rows_data = [
                ["Item", "Category", "Status", "Notes"],
                [title, "General", "Planned", f"Created {dt.date.today().isoformat()}"],
            ]

        for r_idx, row in enumerate(rows_data, start=1):
            for c_idx, val in enumerate(row, start=1):
                # Convert numeric strings cleanly when appropriate
                cell_val: object = val
                if r_idx > 1 and re.fullmatch(r"-?\d+(?:\.\d+)?", val):
                    cell_val = float(val) if "." in val else int(val)
                cell = ws.cell(row=r_idx, column=c_idx, value=cell_val)
                if r_idx == 1:
                    cell.font = Font(bold=True)

        out_path = out_dir / _safe_filename(title, ".xlsx")
        wb.save(str(out_path))
        wb.close()
        return f"Created Excel spreadsheet '{out_path.name}' ({len(rows_data)} rows) at {out_path}."

    if act in ("create_invoice", "invoice"):
        cname = (client_name or "Valued Client").strip()
        inv_num = f"INV-{dt.datetime.now().strftime('%Y%m%d-%H%M')}"
        curr = (currency or "NGN").strip().upper()
        items = _parse_invoice_items(items_or_rows or content)
        total = sum(v for _, v in items)

        lines = [
            f"Invoice Number: {inv_num}",
            f"Date: {dt.date.today().strftime('%d %B %Y')}",
            f"Billed To: {cname}",
            "",
            "## Line Items",
        ]
        for desc, amt in items:
            lines.append(f"- {desc}: {curr} {amt:,.2f}")
        lines.extend([
            "",
            f"## Total Due: {curr} {total:,.2f}",
            "",
            "Thank you for your business.",
        ])
        body = "\n".join(lines)

        base_title = f"Invoice_{cname}_{inv_num}"
        pdf_path = out_dir / _safe_filename(base_title, ".pdf")
        docx_path = out_dir / _safe_filename(base_title, ".docx")
        _write_clean_pdf(pdf_path, f"INVOICE — {cname}", body)
        _write_clean_docx(docx_path, f"INVOICE — {cname}", body)
        return (
            f"Created invoice {inv_num} for {cname} (Total: {curr} {total:,.2f}) as PDF ({pdf_path}) and Word ({docx_path})."
        )

    if act in ("scaffold_project", "create_project", "new_project"):
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", title.strip().lower()).strip("-") or "rose-project"
        proj_dir = out_dir / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        ptype = (project_type or "python").strip().lower()

        if ptype == "web":
            (proj_dir / "index.html").write_text(
                f"<!DOCTYPE html>\n<html lang='en'>\n<head><meta charset='utf-8'><title>{title}</title>"
                "<link rel='stylesheet' href='style.css'></head>\n<body><main><h1>"
                f"{title}</h1><p>Built cleanly with R.O.S.E. starter scaffold.</p></main>"
                "<script src='app.js'></script></body>\n</html>\n",
                encoding="utf-8",
            )
            (proj_dir / "style.css").write_text("body{font-family:system-ui,sans-serif;margin:2rem;background:#0b0f14;color:#f3f4f6;}\n", encoding="utf-8")
            (proj_dir / "app.js").write_text("console.log('Project ready');\n", encoding="utf-8")
        elif ptype in ("fastapi", "api"):
            (proj_dir / "app.py").write_text(
                f"from fastapi import FastAPI\n\napp = FastAPI(title={title!r})\n\n@app.get('/health')\ndef health():\n    return {{'status': 'ok'}}\n",
                encoding="utf-8",
            )
            (proj_dir / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
        else:
            (proj_dir / "main.py").write_text(
                f"def main():\n    print('Hello from {title}!')\n\nif __name__ == '__main__':\n    main()\n",
                encoding="utf-8",
            )
            (proj_dir / "requirements.txt").write_text("", encoding="utf-8")

        (proj_dir / "README.md").write_text(f"# {title}\n\nStarter `{ptype}` project scaffolded on {dt.date.today().isoformat()}.\n", encoding="utf-8")

        git_note = ""
        try:
            subprocess.run(["git", "init"], cwd=str(proj_dir), capture_output=True, timeout=4)
            git_note = " Git repository initialized."
        except Exception:
            pass

        if open_after:
            try:
                subprocess.Popen(["antigravity", str(proj_dir)], close_fds=True)
            except Exception:
                pass

        return f"Scaffolded starter {ptype} project '{slug}' at {proj_dir}.{git_note}"

    return f"Unknown document_creator action: {action}"

