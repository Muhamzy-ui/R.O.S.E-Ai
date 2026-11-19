# R.O.S.E Autofill Copilot (Manifest V3 Chrome Extension)

Replaces the legacy `javascript:` bookmarklet with a native Chrome Manifest V3 extension that works across strict CSP headers, nested iframes (Greenhouse, Workable, etc.), React/Vue/Angular controlled inputs, and open Shadow DOM components.

## One-Time Installation in Chrome

1. Open Google Chrome and navigate to `chrome://extensions`.
2. Turn **ON** **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked** and select this folder:
   `rose_autofill_extension`
4. Click the **R.O.S.E Autofill Copilot** toolbar icon (pin it for quick access).
5. Click **Import rose_profile.json** and select `rose_profile.json` from your R.O.S.E project folder.
6. *(Optional — only needed if testing the local `file:///` pages in `tests/autofill_test_pages/`)*: On `chrome://extensions`, open **Details** for **R.O.S.E Autofill Copilot** and enable **Allow access to file URLs**.

## Usage

- Open any job application (from R.O.S.E Job Command Center's **Open Link** button or manually in Chrome).
- Trigger autofill using **either**:
  - Click the floating **ROSE Fill** pill at the bottom-right of the page, **or**
  - Press **`Alt + Shift + F`**.
- Check the summary toast (e.g., `Filled 9/12 fields. Skipped: resume upload, cover letter.`).
- **Safety:** R.O.S.E **never clicks Submit** and **never attaches files** (Chrome blocks extensions from setting `<input type="file">`). Attach your resume manually, paste your tailored pitch if needed, review, and submit.

