"""Audio generation and retrieval operations for NotebookLM Automator."""

import logging
import time
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)


class AudioManager:
    """Manages audio generation and retrieval for a NotebookLM page."""

    def __init__(self, page: "Page", get_text: Callable[[str], str]):
        self.page = page
        self._get_text = get_text

    def generate(
        self,
        style: Optional[str] = None,
        prompt: Optional[str] = None,
        language: Optional[str] = None,
    ) -> str:
        """Generate an audio overview and return a job ID."""
        edit_icon = self.page.locator(
            "mat-icon:has-text('edit'), mat-icon.edit-button-icon"
        ).first
        if not edit_icon.is_visible():
            edit_btn = self.page.locator(
                "button:has(mat-icon:has-text('edit'))"
            ).first
            if edit_btn.is_visible():
                edit_btn.click()
            else:
                raise RuntimeError(
                    "Could not find Edit/Pencil icon for Audio Overview"
                )
        else:
            edit_icon.click(force=True)

        self.page.wait_for_selector("mat-dialog-container", state="visible")

        if style:
            style_text = self._get_text(f"{style}_radio_button")
            style_button = self.page.locator(
                f"mat-radio-button:has-text('{style_text}')"
            )
            if style_button.count() > 0 and style_button.is_visible():
                style_button.click()
            else:
                raise RuntimeError(
                    f"Could not find {style} radio button for Audio Overview"
                )

        if language:
            select_trigger = self.page.locator("mat-select").first
            if select_trigger.is_visible():
                select_trigger.click()
                self.page.wait_for_selector("mat-option", state="visible")

                option = self.page.locator(
                    f"mat-option:has-text('{language}')")
                if option.is_visible():
                    option.click()
                else:
                    self.page.keyboard.press("Escape")

        if prompt:
            placeholder_snippet = self._get_text("prompt_textarea_placeholder")
            textarea = self.page.locator(
                f"textarea[placeholder*='{placeholder_snippet}']"
            )

            if not textarea.is_visible():
                textarea = self.page.locator(
                    "mat-dialog-container textarea").last

            if textarea.is_visible():
                textarea.fill(prompt)

        generate_btn = self.page.locator(
            f"mat-dialog-actions button:has-text('{self._get_text('generate_button')}')"
        ).last
        if not generate_btn.is_visible():
            generate_btn = self.page.locator("mat-dialog-actions button").last

        generate_btn.click()

        try:
            self.page.wait_for_selector(
                "mat-dialog-container", state="hidden", timeout=5000
            )
        except Exception:
            pass

        time.sleep(2)

        items = self.page.locator(".artifact-library-container")
        count = items.count()

        return str(count)

    def get_status(self, job_id: str) -> Dict[str, str]:
        """Check the status of an audio generation job."""
        try:
            index = int(job_id) - 1
        except ValueError:
            return {"status": "unknown", "error": "Invalid job_id format"}

        parent = self.page.locator("artifact-library")
        children = parent.locator(":scope > *")
        count = children.count()

        if count <= index:
            return {"status": "unknown", "error": "Job ID not found"}

        item = children.nth(index)
        text_content = item.inner_text()

        generating_text = self._get_text("generating_status_text")
        if "sync" in text_content or generating_text in text_content:
            return {"status": "generating"}

        if "play_arrow" in text_content or item.locator(
            "mat-icon:has-text('play_arrow')"
        ).is_visible():
            return {"status": "completed"}

        error_text = self._get_text("error_text")
        if "error" in text_content.lower() or error_text in text_content.lower():
            return {"status": "failed"}

        return {"status": "unknown"}

    def get_download_url(self, job_id: str) -> Optional[str]:
        """Get the direct file URL for generated audio."""
        try:
            index = int(job_id) - 1
        except ValueError:
            logger.error("Invalid job_id format: %s", job_id)
            return None

        parent = self.page.locator("artifact-library")
        items = parent.locator(":scope > *")
        count = items.count()

        if index < 0 or index >= count:
            logger.error("Job ID %s not found (items=%s)", job_id, count)
            return None

        item = items.nth(index)
        try:
            item.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass

        play_btn = item.locator(
            f"button[aria-label='{self._get_text('play_arrow_button')}']"
        ).first

        if not play_btn.is_visible():
            logger.error("Play button not found for job %s", job_id)
            return None

        try:
            play_btn.wait_for(state="visible", timeout=1000)
        except Exception:
            pass

        captured_url: Dict[str, Optional[str]] = {"value": None}

        def route_handler(route, request):
            try:
                if captured_url["value"] is None and request.resource_type == "media":
                    captured_url["value"] = request.url
                    route.abort("blockedbyclient")
                    return
            except Exception:
                pass

            try:
                route.continue_()
            except Exception:
                pass

        pattern = "**/*"

        try:
            self.page.route(pattern, route_handler)

            try:
                play_btn.click()
            except Exception as click_error:
                logger.error(
                    "Failed to click play for job %s: %s", job_id, click_error
                )
                return None

            timeout_seconds = 5
            start_time = time.time()
            while (
                captured_url["value"] is None
                and (time.time() - start_time) < timeout_seconds
            ):
                self.page.wait_for_timeout(100)
        finally:
            try:
                self.page.unroute(pattern, route_handler)
            except Exception:
                pass

        try:
            if captured_url["value"]:
                close_player_button = self.page.locator(
                    f"button[aria-label='{self._get_text('close_audio_player_button')}']"
                )
                if close_player_button.is_visible():
                    close_player_button.first.click()
                return captured_url["value"]

            logger.error("Failed to capture media URL for job %s", job_id)
            return None
        except Exception:
            return None

    def download_file(self, url: str) -> Optional[bytes]:
        """Download the audio file and return its binary content.

        Uses curl subprocess with browser cookies for fast, reliable downloads.
        curl handles HTTP/2, connection reuse, and large files efficiently.

        Args:
            url: The direct download URL for the audio file.

        Returns:
            Binary audio data or None if download failed.
        """
        import subprocess
        import tempfile
        import os

        try:
            logger.info("Starting download: %s", url[:80])

            # Export cookies to Netscape format for curl
            browser_cookies = self.page.context.cookies()

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            ) as cookie_file:
                cookie_file.write("# Netscape HTTP Cookie File\n")
                for c in browser_cookies:
                    domain = c.get("domain", "")
                    flag = "TRUE" if domain.startswith(".") else "FALSE"
                    path = c.get("path", "/")
                    secure = "TRUE" if c.get("secure", False) else "FALSE"
                    expiry = str(int(c.get("expires", 0)))
                    cookie_file.write(
                        f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t"
                        f"{c['name']}\t{c['value']}\n"
                    )
                cookie_path = cookie_file.name

            try:
                # Use curl: fast, handles HTTP/2, follows redirects with cookies
                result = subprocess.run(
                    [
                        "curl",
                        "-sS",              # silent but show errors
                        "-L",               # follow redirects
                        "-b", cookie_path,  # use cookies
                        "--max-time", "180",  # 3 min max
                        "--connect-timeout", "10",
                        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142.0.0.0 Safari/537.36",
                        "-H", "Accept: */*",
                        url,
                    ],
                    capture_output=True,
                    timeout=200,
                )

                if result.returncode != 0:
                    stderr = result.stderr.decode("utf-8", errors="replace")
                    logger.error("curl failed (code %s): %s", result.returncode, stderr)
                    return None

                body = result.stdout

                # Verify we got audio, not HTML error
                if len(body) < 1000 or body[:15].lower().startswith(b"<!doctype"):
                    logger.error("Got HTML instead of audio (size=%d)", len(body))
                    return None

                logger.info("Download complete: %d bytes", len(body))
                return body

            finally:
                try:
                    os.unlink(cookie_path)
                except Exception:
                    pass

        except subprocess.TimeoutExpired:
            logger.error("Download timed out")
            return None
        except FileNotFoundError:
            logger.error("curl not found - install curl")
            return None
        except Exception as e:
            logger.error("Failed to download audio file: %s", e)
            return None

    def clear_studio(self) -> Dict[str, Any]:
        """Delete all generated audio items."""
        removed = 0
        parent = self.page.locator("artifact-library")
        if parent.count() == 0:
            return {"success": False, "count": 0, "message": "No generated items found"}

        max_attempts = 200
        for _ in range(max_attempts):
            items = parent.locator(":scope > *")
            count = items.count()
            if count == 0:
                break

            item = items.first
            try:
                item.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass

            more_btn = item.locator(
                f"button[aria-label='{self._get_text('more_button')}']"
            ).first

            if more_btn.count() == 0 or not more_btn.is_visible():
                logger.warning(
                    "Could not locate more options for generated item.")
                break

            try:
                more_btn.click()
            except Exception as e:
                logger.error(f"Failed to open menu for generated item: {e}")
                break

            delete_menu = self.page.get_by_role(
                "menuitem", name=self._get_text("delete_menu_item")
            ).first

            if delete_menu.count() == 0 or not delete_menu.is_visible():
                logger.warning(
                    "Delete option not found in generated item menu.")
                break

            delete_menu.click()

            confirm_button = self.page.get_by_role(
                "button", name=self._get_text("confirm_delete_button")
            ).first

            if confirm_button.count() == 0 or not confirm_button.is_visible():
                logger.warning("Delete confirmation button not found.")
                break

            try:
                confirm_button.click()
                item.wait_for(state="detached", timeout=2000)
            except Exception as e:
                logger.warning(f"Generated item did not delete cleanly: {e}")

            removed += 1
            self.page.wait_for_timeout(300)

        return {"success": removed > 0, "count": removed}
