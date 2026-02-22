# Video Generation Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Video Overview generation, status polling, and MP4 download to the NotebookLM-API fork.

**Architecture:** Fork `osen77/NotebookLM-API` on GitHub, clone locally, then add a `VideoManager` class in `core/video.py` mirroring `AudioManager`. Before implementing any selector logic, use a debug endpoint to dump the Studio panel's real DOM and map actual selectors. Then wire up three new REST endpoints: `/video/generate`, `/video/status/{job_id}`, `/video/download/{job_id}`.

**Tech Stack:** Python 3.9+, FastAPI, Playwright (sync), uv, Docker/browserless

---

## Task 0: Fork, clone, and verify baseline

**Files:**
- No source files yet — repo setup only

**Step 1: Fork the repo on GitHub**

```bash
gh repo fork osen77/NotebookLM-API --clone --remote
```

Expected output: `✓ Created fork <your-user>/NotebookLM-API` + cloned into `NotebookLM-API/`

**Step 2: Enter the repo and verify Python version**

```bash
cd NotebookLM-API
cat .python-version
uv sync
uv run python -c "import notebooklm_automator; print('OK')"
```

Expected: `OK` with no import errors.

**Step 3: Copy plan into repo**

```bash
mkdir -p docs/plans
cp /c/Users/eduar/OneDrive/zSites/seo_tools/notebooklm/docs/plans/2026-02-21-video-generation.md docs/plans/
```

**Step 4: Commit**

```bash
git add docs/plans/2026-02-21-video-generation.md
git commit -m "docs: add video generation implementation plan"
```

---

## Task 1: Add debug endpoint to discover video selectors

**Goal:** Dump the Studio panel HTML while a Video Overview is visible so we can map real selectors before writing any VideoManager code.

**Files:**
- Modify: `src/notebooklm_automator/api/routes.py`

**Step 1: Add the discovery endpoint**

Open `src/notebooklm_automator/api/routes.py`. After the existing `/debug/screenshot` route, add:

```python
@router.get("/debug/studio-video-html")
def debug_studio_video_html(automator: NotebookLMAutomator = Depends(get_automator)):
    """Dump Studio panel HTML to discover video selectors.

    Navigate to the Studio tab and return the full innerHTML so we can
    map the real CSS selectors for video generation.
    """
    try:
        automator.ensure_connected()
        page = automator.page

        # Switch to Studio tab using the same helper audio uses
        automator._audio_manager._ensure_studio_tab()
        page.wait_for_timeout(2000)

        # Dump artifact-library or entire studio panel
        studio_html = ""
        artifact_lib = page.locator("artifact-library")
        if artifact_lib.count() > 0:
            studio_html = artifact_lib.inner_html()
        else:
            # Fallback: dump entire body (truncated)
            studio_html = page.locator("body").inner_html()[:5000]

        # Also capture all button texts visible on page
        buttons = page.locator("button").all()
        button_texts = [b.inner_text().strip() for b in buttons if b.inner_text().strip()]

        # Capture all role=tab texts
        tabs = page.locator("[role='tab']").all()
        tab_texts = [t.inner_text().strip() for t in tabs if t.inner_text().strip()]

        return {
            "studio_html": studio_html,
            "button_texts": button_texts,
            "tab_texts": tab_texts,
            "page_url": page.url,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Step 2: Start the server and hit the endpoint**

```bash
uv run run-server
# In another terminal:
curl http://localhost:8000/debug/studio-video-html | python -m json.tool
```

Look for:
- A "Generate video" (or equivalent) button text
- The language dropdown selector or button
- The `artifact-library` children structure for video items
- Any `mat-icon` or class names unique to video vs audio

**Step 3: Record findings**

After inspecting the output, fill in the selector values in Task 2 below. Update the plan with real values before continuing.

**Step 4: Commit**

```bash
git add src/notebooklm_automator/api/routes.py
git commit -m "debug: add studio-video-html endpoint for selector discovery"
```

---

## Task 2: Add video selector keys

**Goal:** Register the video UI selector strings in `selectors.py` so `VideoManager` can use `_get_text(key)`.

**Files:**
- Modify: `src/notebooklm_automator/core/selectors.py`

**Step 1: Read the current selectors file**

```bash
cat src/notebooklm_automator/core/selectors.py
```

Understand the structure — it's a dict keyed by language code, with string values for button labels/texts.

**Step 2: Add video keys**

Using the button texts discovered in Task 1, add entries for:

```python
# In each language dict (start with "en", add "he" equivalent if known):
"video_generate_button": "Generate video",      # UPDATE with real text from Task 1
"video_language_button": "Language",            # UPDATE with real text from Task 1
"video_confirm_button": "Generate",             # UPDATE with real text from Task 1
"video_downloading": "Downloading",             # Status text while video renders
"video_ready": "Download",                      # Text on download button when complete
```

**Step 3: Verify selectors load correctly**

```bash
uv run python -c "
from notebooklm_automator.core.selectors import get_selector_by_language
print(get_selector_by_language('en', 'video_generate_button'))
"
```

Expected: prints the button text string, no errors.

**Step 4: Commit**

```bash
git add src/notebooklm_automator/core/selectors.py
git commit -m "feat: add video selector keys to selectors.py"
```

---

## Task 3: Implement VideoManager

**Goal:** `core/video.py` with generate, get_status, and download_file methods.

**Files:**
- Create: `src/notebooklm_automator/core/video.py`

**Step 1: Create the file**

```python
"""Video Overview manager for NotebookLM."""

import logging
import time
from typing import Callable, Dict, Optional, Tuple

from playwright.sync_api import Page, Error as PlaywrightError

logger = logging.getLogger(__name__)


class VideoManager:
    """Manages Video Overview generation and download in NotebookLM Studio."""

    def __init__(self, page: Page, get_text: Callable[[str], str]):
        self._page = page
        self._get_text = get_text
        self._jobs: Dict[str, Dict] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """Trigger Video Overview generation.

        Args:
            language: Optional language string (e.g. "Español (Latinoamérica)").
            prompt: Optional prompt text (if the UI supports it).

        Returns:
            job_id string for tracking.
        """
        self._ensure_studio_tab()

        generate_text = self._get_text("video_generate_button")
        btn = self._page.locator(f"button:has-text('{generate_text}')").first
        btn.wait_for(state="visible", timeout=10000)
        btn.click()
        logger.info("Clicked video generate button")

        # Handle language selection if dialog/dropdown appears
        if language:
            self._select_language(language)

        # Handle prompt field if present
        if prompt:
            self._fill_prompt(prompt)

        # Click confirm/generate
        self._click_confirm()

        job_id = str(int(time.time() * 1000))
        self._jobs[job_id] = {"status": "generating", "index": -1}
        logger.info(f"Video generation started, job_id={job_id}")
        return job_id

    def get_status(self, job_id: str) -> Dict[str, str]:
        """Check generation status for a job.

        Returns dict with keys: status (generating|completed|failed), title (optional).
        """
        if job_id not in self._jobs:
            return {"status": "unknown"}

        self._ensure_studio_tab()

        try:
            # Video items sit alongside audio in artifact-library
            # We look for items that have a video indicator (e.g. videocam icon)
            # UPDATE selector after Task 1 discovery
            items = self._page.locator("artifact-library > *").all()

            for i, item in enumerate(items):
                html = item.inner_html()
                # Heuristic: video items contain "videocam" mat-icon or similar
                if "videocam" not in html and "video" not in html.lower():
                    continue

                # Check if still generating (spinner present)
                spinner = item.locator("[class*='spinner'], [class*='progress']")
                if spinner.count() > 0:
                    self._jobs[job_id]["status"] = "generating"
                    self._jobs[job_id]["index"] = i
                    return {"status": "generating"}

                # Check if download button present (completed)
                download_text = self._get_text("video_ready")
                dl_btn = item.locator(f"button:has-text('{download_text}')")
                if dl_btn.count() > 0:
                    self._jobs[job_id]["status"] = "completed"
                    self._jobs[job_id]["index"] = i
                    # Try to get title
                    try:
                        title = item.locator("[class*='title'], h3, h4").first.inner_text()
                    except Exception:
                        title = f"video_{job_id}"
                    return {"status": "completed", "title": title}

            # No matching item found yet
            return {"status": "generating"}

        except Exception as e:
            logger.warning(f"Error checking video status: {e}")
            return {"status": "failed"}

    def download_file(self, job_id: str) -> Optional[Tuple[bytes, str, int]]:
        """Click the Download button for the video and capture the file.

        Returns:
            Tuple of (bytes, filename, size) or None on failure.
        """
        if job_id not in self._jobs:
            logger.error(f"Job {job_id} not found")
            return None

        self._ensure_studio_tab()

        # Find the video item
        index = self._jobs[job_id].get("index", -1)
        if index < 0:
            # Re-scan to find it
            status = self.get_status(job_id)
            if status["status"] != "completed":
                logger.error(f"Job {job_id} not completed: {status}")
                return None
            index = self._jobs[job_id].get("index", -1)

        try:
            items = self._page.locator("artifact-library > *").all()
            if index >= len(items):
                logger.error(f"Item index {index} out of range ({len(items)} items)")
                return None

            item = items[index]

            # Open the "⋮" overflow menu
            menu_btn = item.locator("button[aria-label*='more'], button[aria-label*='More'], mat-icon:has-text('more_vert')").first
            menu_btn.click()
            self._page.wait_for_timeout(500)

            # Click "Download" in the menu
            download_text = self._get_text("video_ready")
            dl_option = self._page.locator(
                f"[role='menuitem']:has-text('{download_text}'), "
                f"button:has-text('{download_text}')"
            ).first
            dl_option.wait_for(state="visible", timeout=5000)

            # Intercept the download
            with self._page.expect_download(timeout=120000) as dl_info:
                dl_option.click()

            download = dl_info.value
            file_name = download.suggested_filename or f"video_{job_id}.mp4"

            # Read bytes
            path = download.path()
            with open(path, "rb") as f:
                content = f.read()

            file_size = len(content)
            logger.info(f"Downloaded video: {file_name} ({file_size} bytes)")
            return content, file_name, file_size

        except Exception as e:
            logger.error(f"Failed to download video for job {job_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_studio_tab(self) -> None:
        """Switch to the Studio tab if not already active."""
        # Reuse the same logic as AudioManager
        try:
            studio_text = self._get_text("studio_tab")
            tab = self._page.locator(
                f".mat-mdc-tab:has-text('{studio_text}'), "
                f"[role='tab']:has-text('{studio_text}')"
            ).first
            if tab.count() == 0:
                return
            aria = tab.get_attribute("aria-selected")
            if aria != "true":
                tab.click()
                self._page.wait_for_timeout(1500)
        except Exception as e:
            logger.warning(f"Could not switch to Studio tab: {e}")

    def _select_language(self, language: str) -> None:
        """Select language in the video generation dialog."""
        try:
            lang_text = self._get_text("video_language_button")
            # Try a mat-select or button that opens a language dropdown
            lang_btn = self._page.locator(
                f"[aria-label*='language' i], "
                f"mat-select[aria-label*='language' i], "
                f"button:has-text('{lang_text}')"
            ).first
            if lang_btn.count() == 0:
                logger.warning("Language selector not found, skipping")
                return
            lang_btn.click()
            self._page.wait_for_timeout(500)
            # Click the matching option
            option = self._page.locator(
                f"mat-option:has-text('{language}'), "
                f"[role='option']:has-text('{language}')"
            ).first
            option.wait_for(state="visible", timeout=5000)
            option.click()
            logger.info(f"Selected language: {language}")
        except Exception as e:
            logger.warning(f"Language selection failed: {e}")

    def _fill_prompt(self, prompt: str) -> None:
        """Fill the prompt field in the video generation dialog if present."""
        try:
            field = self._page.locator(
                "textarea[placeholder*='prompt' i], "
                "input[placeholder*='prompt' i]"
            ).first
            if field.count() == 0:
                logger.warning("Prompt field not found, skipping")
                return
            field.fill(prompt)
            logger.info("Filled prompt field")
        except Exception as e:
            logger.warning(f"Prompt fill failed: {e}")

    def _click_confirm(self) -> None:
        """Click the final confirm/generate button in the dialog."""
        try:
            confirm_text = self._get_text("video_confirm_button")
            btn = self._page.locator(
                f"button:has-text('{confirm_text}')"
            ).last  # Usually the rightmost/last button in a dialog
            btn.wait_for(state="visible", timeout=5000)
            btn.click()
            self._page.wait_for_timeout(1000)
            logger.info("Clicked confirm button")
        except Exception as e:
            logger.warning(f"Confirm click failed: {e}")
```

**Step 2: Verify the module imports cleanly**

```bash
uv run python -c "from notebooklm_automator.core.video import VideoManager; print('OK')"
```

Expected: `OK`

**Step 3: Commit**

```bash
git add src/notebooklm_automator/core/video.py
git commit -m "feat: add VideoManager for video overview generation and download"
```

---

## Task 4: Wire VideoManager into NotebookLMAutomator

**Files:**
- Modify: `src/notebooklm_automator/core/automator.py`

**Step 1: Import VideoManager**

At the top of `automator.py`, after the `AudioManager` import line, add:

```python
from notebooklm_automator.core.video import VideoManager
```

**Step 2: Add instance variable**

In `__init__`, after `self._audio_manager: Optional[AudioManager] = None`, add:

```python
self._video_manager: Optional[VideoManager] = None
```

**Step 3: Init in `_init_managers`**

In the `_init_managers` method, after the `AudioManager` line, add:

```python
self._video_manager = VideoManager(self.page, self._get_text)
```

**Step 4: Nullify on close**

In the `close` method, after `self._audio_manager = None`, add:

```python
self._video_manager = None
```

**Step 5: Add public methods**

After the `clear_studio` method at the bottom of the class, add:

```python
def generate_video(
    self,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
) -> str:
    """Trigger a Video Overview generation.

    Args:
        language: Optional language string (e.g. "Español (Latinoamérica)").
        prompt: Optional prompt text.

    Returns:
        Job ID string for tracking.
    """
    self.ensure_connected()
    return self._video_manager.generate(language=language, prompt=prompt)

def get_video_status(self, job_id: str) -> Dict[str, str]:
    """Check the status of a video generation job.

    Args:
        job_id: The job ID returned from generate_video.

    Returns:
        Dict with 'status' key (generating, completed, failed, unknown).
    """
    self.ensure_connected()
    return self._video_manager.get_status(job_id)

def download_video_file(self, job_id: str) -> Optional[Tuple[bytes, str, int]]:
    """Download the video file by clicking Download in the UI.

    Args:
        job_id: The job ID of the video to download.

    Returns:
        Tuple of (file_content, file_name, file_size) or None if failed.
    """
    self.ensure_connected()
    return self._video_manager.download_file(job_id)
```

**Step 6: Verify import**

```bash
uv run python -c "from notebooklm_automator.core.automator import NotebookLMAutomator; print('OK')"
```

Expected: `OK`

**Step 7: Commit**

```bash
git add src/notebooklm_automator/core/automator.py
git commit -m "feat: wire VideoManager into NotebookLMAutomator"
```

---

## Task 5: Add Pydantic models for video endpoints

**Files:**
- Modify: `src/notebooklm_automator/api/models.py`

**Step 1: Read models.py first**

```bash
cat src/notebooklm_automator/api/models.py
```

Understand existing patterns (GenerateAudioRequest, AudioStatusResponse, etc.)

**Step 2: Add video models**

After the last audio model, add:

```python
class GenerateVideoRequest(BaseModel):
    language: Optional[str] = Field(
        None,
        description="Language for the video narration (e.g. 'Español (Latinoamérica)')",
    )
    prompt: Optional[str] = Field(
        None,
        description="Optional customization prompt for the video",
    )


class GenerateVideoResponse(BaseModel):
    job_id: str
    status: str


class VideoStatusResponse(BaseModel):
    job_id: str
    status: str
    title: Optional[str] = None
```

**Step 3: Verify models import**

```bash
uv run python -c "
from notebooklm_automator.api.models import (
    GenerateVideoRequest, GenerateVideoResponse, VideoStatusResponse
)
print('OK')
"
```

Expected: `OK`

**Step 4: Commit**

```bash
git add src/notebooklm_automator/api/models.py
git commit -m "feat: add Pydantic models for video generation endpoints"
```

---

## Task 6: Add video API routes

**Files:**
- Modify: `src/notebooklm_automator/api/routes.py`

**Step 1: Add imports**

At the top of `routes.py`, in the models import block, add:

```python
    GenerateVideoRequest,
    GenerateVideoResponse,
    VideoStatusResponse,
```

**Step 2: Add the three video routes**

After the `/studio/clear` route, add:

```python
@router.post("/video/generate", response_model=GenerateVideoResponse)
def generate_video(
    request: GenerateVideoRequest,
    automator: NotebookLMAutomator = Depends(get_automator),
):
    """Trigger video overview generation."""
    try:
        job_id = automator.generate_video(
            language=request.language,
            prompt=request.prompt,
        )
        return GenerateVideoResponse(job_id=job_id, status="started")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/video/status/{job_id}", response_model=VideoStatusResponse)
def check_video_status(
    job_id: str,
    automator: NotebookLMAutomator = Depends(get_automator),
):
    """Check the status of a video generation job."""
    status_data = automator.get_video_status(job_id)
    return VideoStatusResponse(
        job_id=job_id,
        status=status_data["status"],
        title=status_data.get("title"),
    )


@router.get("/video/download/{job_id}")
def download_video_file(
    job_id: str,
    automator: NotebookLMAutomator = Depends(get_automator),
):
    """Download the generated video as an MP4 binary."""
    from urllib.parse import quote

    status_data = automator.get_video_status(job_id)
    if status_data["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail="Video generation not completed or failed",
        )

    result = automator.download_video_file(job_id)
    if not result:
        raise HTTPException(
            status_code=500,
            detail="Failed to download video file",
        )

    content, file_name, file_size = result
    encoded_filename = quote(file_name, safe="")
    content_disposition = (
        f"attachment; filename=\"{encoded_filename}\"; "
        f"filename*=UTF-8''{encoded_filename}"
    )

    return Response(
        content=content,
        media_type="video/mp4",
        headers={
            "Content-Disposition": content_disposition,
            "Content-Length": str(file_size),
        },
    )
```

**Step 3: Verify FastAPI app starts cleanly**

```bash
uv run python -c "
from notebooklm_automator.api.app import app
routes = [r.path for r in app.routes]
assert '/video/generate' in routes or any('video' in r for r in routes), routes
print('Routes OK:', [r for r in routes if 'video' in r])
"
```

Expected: prints the three video routes.

**Step 4: Commit**

```bash
git add src/notebooklm_automator/api/routes.py
git commit -m "feat: add /video/generate, /video/status, /video/download routes"
```

---

## Task 7: Selector refinement pass

**Goal:** Replace placeholder selector values with the real ones discovered in Task 1.

**Step 1: Start the server against your real notebook**

```bash
NOTEBOOKLM_URL="https://notebooklm.google.com/notebook/<YOUR_ID>" uv run run-server
```

**Step 2: Hit the discovery endpoint**

```bash
curl http://localhost:8000/debug/studio-video-html | python -m json.tool > /tmp/studio_dump.json
cat /tmp/studio_dump.json
```

**Step 3: Update selectors.py**

In `core/selectors.py`, replace the placeholder values in Task 2 with the real button texts found in `button_texts`.

Key things to confirm:
- Exact text on the "Generate video" button
- Whether the language selector is a `mat-select`, a `button`, or a dropdown
- CSS class or `mat-icon` name that distinguishes video items from audio items in `artifact-library`
- Text on the Download menu item inside the `⋮` overflow menu

**Step 4: Update VideoManager selectors if needed**

If `artifact-library > *` is wrong (e.g. items are nested differently), update the locator strings in `core/video.py` `get_status` and `download_file` methods.

**Step 5: Smoke-test end-to-end**

```bash
# 1. Trigger generation
curl -X POST http://localhost:8000/video/generate \
  -H "Content-Type: application/json" \
  -d '{"language": "Español (Latinoamérica)"}' | python -m json.tool

# 2. Poll status (replace JOB_ID)
curl http://localhost:8000/video/status/JOB_ID | python -m json.tool

# 3. Download when completed
curl -o video_test.mp4 http://localhost:8000/video/download/JOB_ID
file video_test.mp4
```

Expected: `video_test.mp4: ISO Media, MP4 Base Media v1` (or similar).

**Step 6: Commit**

```bash
git add src/notebooklm_automator/core/selectors.py src/notebooklm_automator/core/video.py
git commit -m "fix: refine video selectors after real UI discovery"
```

---

## Task 8: Update README

**Files:**
- Modify: `README.md`

**Step 1: Add video section after the existing audio endpoints table**

Find the section documenting audio endpoints and add below it:

```markdown
### Video Overviews

| Method | Path | Description |
|--------|------|-------------|
| POST | `/video/generate` | Trigger Video Overview generation |
| GET | `/video/status/{job_id}` | Poll generation status |
| GET | `/video/download/{job_id}` | Download finished MP4 |

**Example:**
```bash
# 1. Start generation (language is optional)
curl -X POST http://localhost:8000/video/generate \
  -H "Content-Type: application/json" \
  -d '{"language": "Español (Latinoamérica)"}'
# → {"job_id": "1708531200000", "status": "started"}

# 2. Poll until completed (can take several minutes)
curl http://localhost:8000/video/status/1708531200000
# → {"job_id": "...", "status": "completed", "title": "Video overview"}

# 3. Download the MP4
curl -o overview.mp4 http://localhost:8000/video/download/1708531200000
```
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document video generation API endpoints"
```

---

## Task 9: Push fork and open PR

**Step 1: Push to your fork**

```bash
git push origin main
```

**Step 2: Open a PR against upstream (optional)**

```bash
gh pr create \
  --repo osen77/NotebookLM-API \
  --title "feat: add Video Overview generation, status, and download endpoints" \
  --body "Adds VideoManager and three new REST endpoints (/video/generate, /video/status, /video/download) mirroring the existing audio flow. Language selection supported (e.g. Español Latinoamericano). MP4 downloaded by clicking the UI Download button via Playwright."
```

---

## Selector Reference (fill in after Task 1 discovery)

| Key | English value (placeholder) | Notes |
|-----|----------------------------|-------|
| `video_generate_button` | `"Generate video"` | UPDATE after discovery |
| `video_language_button` | `"Language"` | UPDATE after discovery |
| `video_confirm_button` | `"Generate"` | UPDATE after discovery |
| `video_ready` | `"Download"` | Text on download button/menu item |
| video item discriminator | `"videocam"` in innerHTML | UPDATE with real class/icon name |
