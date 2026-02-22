"""Video Overview manager for NotebookLM."""

import logging
import time
from typing import Callable, Dict, Optional, Tuple

from playwright.sync_api import Page

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
            language: Optional language string (e.g. "Espanol (Latinoamerica)").
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
                    try:
                        title = item.locator("[class*='title'], h3, h4").first.inner_text()
                    except Exception:
                        title = f"video_{job_id}"
                    return {"status": "completed", "title": title}

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

        index = self._jobs[job_id].get("index", -1)
        if index < 0:
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

            # Open the overflow menu
            menu_btn = item.locator(
                "button[aria-label*='more' i], mat-icon:has-text('more_vert')"
            ).first
            menu_btn.click()
            self._page.wait_for_timeout(500)

            # Click "Download" in the menu
            download_text = self._get_text("video_ready")
            dl_option = self._page.locator(
                f"[role='menuitem']:has-text('{download_text}'), "
                f"button:has-text('{download_text}')"
            ).first
            dl_option.wait_for(state="visible", timeout=5000)

            with self._page.expect_download(timeout=120000) as dl_info:
                dl_option.click()

            download = dl_info.value
            file_name = download.suggested_filename or f"video_{job_id}.mp4"

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
            ).last
            btn.wait_for(state="visible", timeout=5000)
            btn.click()
            self._page.wait_for_timeout(1000)
            logger.info("Clicked confirm button")
        except Exception as e:
            logger.warning(f"Confirm click failed: {e}")
