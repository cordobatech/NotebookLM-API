"""Video Overview manager for NotebookLM."""

import logging
import os
import time
from typing import Callable, Dict, Optional, Tuple

from playwright.sync_api import Page

logger = logging.getLogger(__name__)

# Download directory (mirrors AudioManager)
_DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/shared-downloads")

# Index of the Video card's edit button among all studio edit buttons.
# Studio panel layout (left-to-right, top-to-bottom):
#   0: Audio  1: Slide Deck  2: Video  3: Mind Map  4: Flashcards  5: Quiz ...
_VIDEO_EDIT_BUTTON_INDEX = 2


class VideoManager:
    """Manages Video Overview generation and download in NotebookLM Studio.

    Mirrors AudioManager patterns exactly:
    - Clicks the pencil/edit icon on the Video card to open the generation dialog
    - Uses 1-based item index as job_id (matches artifact-library position)
    - Polls artifact-library items by index for status
    - Downloads by opening the More menu and watching the filesystem
    """

    def __init__(self, page: Page, get_text: Callable[[str], str]):
        self._page = page
        self._get_text = get_text

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
            language: Optional language (e.g. "Español (Latinoamérica)").
            prompt: Optional customization prompt.

        Returns:
            1-based job_id string (index of the new item in artifact-library).
        """
        self._ensure_studio_tab()

        # Click the pencil/edit icon for the Video card.
        # Audio is edit[0]; Video is edit[2] in the Studio grid.
        edit_btns = self._page.locator("button:has(mat-icon:has-text('edit'))")
        video_edit = edit_btns.nth(_VIDEO_EDIT_BUTTON_INDEX)

        if not video_edit.is_visible():
            # Fallback: mat-icon directly
            video_edit = self._page.locator(
                "mat-icon:has-text('edit'), mat-icon.edit-button-icon"
            ).nth(_VIDEO_EDIT_BUTTON_INDEX)

        if not video_edit.is_visible():
            raise RuntimeError("Could not find the Video card edit/pencil icon")

        video_edit.click(force=True)
        self._page.wait_for_selector("mat-dialog-container", state="visible")
        logger.info("Video generation dialog opened")

        # Language selection
        if language:
            select_trigger = self._page.locator("mat-select").first
            if select_trigger.is_visible():
                select_trigger.click()
                self._page.wait_for_selector("mat-option", state="visible")
                option = self._page.locator(f"mat-option:has-text('{language}')")
                if option.is_visible():
                    option.click()
                    logger.info(f"Selected language: {language}")
                else:
                    logger.warning(f"Language option '{language}' not found")
                    self._page.keyboard.press("Escape")

        # Prompt
        if prompt:
            textarea = self._page.locator("mat-dialog-container textarea").last
            if textarea.is_visible():
                textarea.fill(prompt)
                logger.info("Filled prompt field")

        # Click the Generate button
        generate_text = self._get_text("generate_button")
        generate_btn = self._page.locator(
            f"mat-dialog-actions button:has-text('{generate_text}')"
        ).last
        if not generate_btn.is_visible():
            generate_btn = self._page.locator("mat-dialog-actions button").last

        generate_btn.click()
        logger.info("Clicked generate button")

        # Wait for dialog to close
        try:
            self._page.wait_for_selector(
                "mat-dialog-container", state="hidden", timeout=5000
            )
        except Exception:
            pass

        time.sleep(2)

        # Job ID = new item count (1-based index of the newly added item)
        parent = self._page.locator("artifact-library")
        count = parent.locator(":scope > *").count()
        job_id = str(count)
        logger.info(f"Video generation started, job_id={job_id}")
        return job_id

    def get_status(self, job_id: str) -> Dict[str, str]:
        """Check generation status for a job.

        Args:
            job_id: 1-based index string returned by generate().

        Returns:
            Dict with 'status' (generating|completed|failed|unknown) and optional 'title'.
        """
        self._ensure_studio_tab()

        try:
            index = int(job_id) - 1
        except ValueError:
            return {"status": "unknown", "error": "Invalid job_id format"}

        parent = self._page.locator("artifact-library")
        children = parent.locator(":scope > *")
        count = children.count()

        if count <= index:
            return {"status": "unknown", "error": "Job ID not found"}

        item = children.nth(index)
        text_content = item.inner_text()
        title = self._get_item_title(item)

        # Generating: spinner/sync icon present
        generating_text = self._get_text("generating_status_text")
        if "sync" in text_content or generating_text in text_content:
            return {"status": "generating", "title": title}

        # Completed: play_arrow button visible
        if "play_arrow" in text_content or item.locator(
            "mat-icon:has-text('play_arrow')"
        ).is_visible():
            return {"status": "completed", "title": title}

        # Failed
        error_text = self._get_text("error_text")
        if "error" in text_content.lower() or error_text in text_content.lower():
            return {"status": "failed", "title": title}

        return {"status": "unknown", "title": title}

    def download_file(self, job_id: str) -> Optional[Tuple[bytes, str, int]]:
        """Download the video file by clicking More → Download.

        Mirrors AudioManager.download_file(): opens the More menu, clicks
        Download, then watches the filesystem for the new file.

        Args:
            job_id: 1-based index string returned by generate().

        Returns:
            Tuple of (bytes, filename, size) or None on failure.
        """
        self._ensure_studio_tab()

        try:
            index = int(job_id) - 1
        except ValueError:
            logger.error("Invalid job_id: %s", job_id)
            return None

        # Snapshot files BEFORE download
        try:
            files_before = set(os.listdir(_DOWNLOAD_DIR))
        except Exception:
            files_before = set()

        try:
            parent = self._page.locator("artifact-library")
            items = parent.locator(":scope > *")
            count = items.count()

            if index < 0 or index >= count:
                logger.error("Job %s not found (count=%d)", job_id, count)
                return None

            item = items.nth(index)
            try:
                item.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass

            # Open the More menu
            more_text = self._get_text("more_button")
            more_btn = item.locator(f"button[aria-label='{more_text}']").first

            if not more_btn.is_visible():
                logger.error("More button not found for job %s", job_id)
                return None

            more_btn.click()
            self._page.wait_for_timeout(500)

            # Click Download in the menu
            download_text = self._get_text("download_menu_item")
            download_menu = self._page.get_by_role(
                "menuitem", name=download_text
            ).first

            if not download_menu.is_visible():
                logger.error("Download menu item not found")
                self._page.keyboard.press("Escape")
                return None

            logger.info("Clicking Download...")
            download_menu.click()

            # Watch filesystem for new file
            logger.info("Waiting for file in %s...", _DOWNLOAD_DIR)
            timeout = 120
            start_time = time.time()
            downloaded_file = None
            files_now: set = set()

            while time.time() - start_time < timeout:
                try:
                    files_now = set(os.listdir(_DOWNLOAD_DIR))
                    new_files = files_now - files_before
                    for f in new_files:
                        if f.endswith(".crdownload") or f.startswith("."):
                            continue
                        filepath = os.path.join(_DOWNLOAD_DIR, f)
                        if os.path.isfile(filepath) and os.path.getsize(filepath) > 0:
                            downloaded_file = filepath
                            logger.info("Found new file: %s", f)
                            break
                except Exception as e:
                    logger.debug("Error listing dir: %s", e)

                if downloaded_file:
                    time.sleep(1)  # Ensure write complete
                    break
                time.sleep(0.5)

            if not downloaded_file:
                logger.error(
                    "Download timed out. Before: %s, After: %s",
                    files_before, files_now,
                )
                return None

            with open(downloaded_file, "rb") as f:
                body = f.read()
            file_name = os.path.basename(downloaded_file)
            file_size = os.path.getsize(downloaded_file)
            logger.info("Downloaded %d bytes from %s", len(body), downloaded_file)

            try:
                os.remove(downloaded_file)
            except Exception:
                pass

            return body, file_name, file_size

        except Exception as e:
            logger.error("Download failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_studio_tab(self) -> None:
        """Switch to Studio tab if artifact-library is not visible (mirrors AudioManager)."""
        parent = self._page.locator("artifact-library")
        if parent.count() > 0:
            return  # Already in full layout or Studio tab is active

        studio_text = self._get_text("studio_tab")
        studio_tab = self._page.locator(
            f".mat-mdc-tab:has-text('{studio_text}'), "
            f".mat-tab-label:has-text('{studio_text}'), "
            f"[role='tab']:has-text('{studio_text}')"
        ).first
        if studio_tab.count() > 0 and studio_tab.is_visible():
            studio_tab.click()
            self._page.wait_for_timeout(500)
            return

        # Fallback to text match
        studio_tab = self._page.get_by_text(studio_text, exact=True).first
        if studio_tab.count() > 0 and studio_tab.is_visible():
            studio_tab.click()
            self._page.wait_for_timeout(500)

    def _get_item_title(self, item) -> Optional[str]:
        """Extract title from an artifact-library-item element."""
        try:
            for selector in [
                ".artifact-title",
                "span.artifact-title",
                ".artifact-labels .artifact-title",
                "span.mat-title-small",
            ]:
                title_el = item.locator(selector).first
                if title_el.count() > 0 and title_el.is_visible():
                    title = title_el.inner_text().strip()
                    if title:
                        return title
        except Exception:
            pass
        return None
