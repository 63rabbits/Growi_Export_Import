"""
Functional verification was performed in the following environment:
- GROWI 8.0.1
- Python 3.12.13
- macOS Tahoe
"""

from __future__ import annotations

import sys
import os
import io
import re
import time
import argparse
import configparser
import json
import unicodedata
from configparser import NoOptionError

import requests

from typing import Any, Optional
from datetime import datetime
from urllib3 import Retry
from pathlib import Path
from requests.adapters import HTTPAdapter


# ==================== Settings ====================
PC_MAX_PATH_LEN     = 260   # Windows limit
# In Windows 10 (version 1607 or later) and Windows 11, it is possible to remove
# the standard maximum path length limit (260 characters / MAX_PATH) and
# extend it to a maximum of 32,767 characters.
# 1. Run the following command in PowerShell.
#   New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem"
#                    -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
# 2. Restart the computer.

DEFAULT_TIMEOUT     = 10.0  # seconds, session timeout
API_SLEEP_INTERVAL  = 0.1   # seconds, Reducing server load

HTTP_RES_SUCCESS        = 200

NAME_PAGE_JSON          = "page.json"
NAME_ATTACHMENTS_JSON   = "attachments.json"
NAME_COMMENTS_JSON      = "comments.json"
NAME_TAGS_JSON          = "tags.json"
NAME_BOOKMARKS_JSON     = "bookmarks.json"
NAME_MARKDOWN           = "markdown.md"
NAME_ATTACHMENT         = "attachment"


class GrowiExport:

    def __init__(self, growi_url: str, growi_path: str, access_token: str, export_dir: str, logger: TerminalFileLogger,
                 normalization_form: str):
        self._os_name = self._get_os_type()
        self._pc_max_path_len = self._get_max_path_len()

        self._normalization_form = normalization_form
        self._growi_url = growi_url.rstrip("/")
        self._growi_path = growi_path.rstrip("/")
        self._access_token = access_token
        self._export_dir = str(Path(self._auto_normalize_dir(export_dir) or "").absolute())
        self._logger = logger
        self._session = self._create_session()

    @staticmethod
    def _get_os_type() -> str:
        if sys.platform.startswith("win"):
            return "Windows"
        elif sys.platform == "darwin":
            return "macOS"
        elif sys.platform.startswith("linux"):
            return "Linux"

        return "unknown"

    @staticmethod
    def _get_max_path_len() -> int:
        pc_max_path_len = PC_MAX_PATH_LEN
        try:
            pc_max_path_len = os.pathconf('/', 'PC_PATH_MAX')     # Max Path Length　(including the file name)
        except (AttributeError, OSError, ValueError):
            pass

        return pc_max_path_len

    def _create_session(self) -> requests.Session:
        """Creates a requests.Session with automatic retry logic."""
        session = requests.Session()
        session.headers.update({
            "Accept": "application/json",
        })
        if self._access_token:
            session.headers.update({
                "Authorization": f"Bearer {self._access_token}",
            })

        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            # 500: Internal Server Error
            # 502: Bad Gateway
            # 503: Service Unavailable
            # 504: Gateway Timeout
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("http://", adapter)   # for on-premises
        session.mount("https://", adapter)  # for GROWI.cloud
        return session

    def _get_pages_under_path(self, grow_path: str, limit: int = 100) -> list:
        """Retrieves a list of pages under the specified path (including itself)."""
        page_num = 0
        target_pages = []

        while True:
            page_num += 1

            res = self._session.get(
                f"{self._growi_url}/_api/v3/pages/list",
                params={
                    "path": grow_path,
                    "limit": limit,
                    "page": page_num,
                }
            )
            res_status = res.status_code
            if res_status != HTTP_RES_SUCCESS:
                self._logger.log(f"{res_status}")
                self._logger.log(f"    [RETRIEVE] ### FAILED ### Failed to retrieve page list."
                                 f" (HTTP status code : {res_status})")
                return target_pages

            current_pages = res.json().get("pages", [])

            for page in current_pages:
                page_path = page.get("path")
                if page_path == grow_path or page_path.startswith(grow_path + "/"):
                    target_pages.append({
                        "_id": page.get("_id"),
                        "path": page_path
                    })

            if len(current_pages) < limit:
                break

            time.sleep(API_SLEEP_INTERVAL)  # Reducing server load

        target_pages.sort(key=lambda x: x["path"])

        return target_pages

    def _export_page(self, page_id: str, save_dir: str) -> bool:
        """Saves the specified page."""
        res = self._session.get(
            f"{self._growi_url}/_api/v3/page",
            params={
                "pageId": page_id
            }
        )
        res_status = res.status_code
        if res_status != HTTP_RES_SUCCESS:
            self._logger.log(f"    [Page] ### FAILED ### Failed to retrieve page. (HTTP status code : {res_status})")
            return False

        res_json = res.json()

        # Save as JSON
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, NAME_PAGE_JSON)
        if (save_path := self._auto_normalize_dir(save_path)) is None:
            return False
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(res_json, f, indent=2, ensure_ascii=False)

        # Create Markdown
        page_info = res_json.get("page")
        page_name = Path(page_info.get("path")).name
        markdown_body = page_info.get("revision").get("body")
        if not markdown_body:
            self._logger.log(f"    [Page] Save page information only.", to_terminal=False)
            return True

        markdown_path = os.path.join(save_dir, f"{page_name}.md")
        if (markdown_path := self._auto_normalize_dir(markdown_path)) is None:
            return False

        path = Path(markdown_path)
        path.write_text(markdown_body, encoding="utf-8")
        self._logger.log(f"    [Page] Markdown: {path.name}", to_terminal=False)

        return True

    def _export_attachments(self, page_id: str, save_dir: str) -> bool:
        """Export attachments for the specified page."""
        res = self._session.get(
            f"{self._growi_url}/_api/v3/attachment/list",
            params={
                "pageId": page_id
            }
        )
        res_status = res.status_code
        if res_status != HTTP_RES_SUCCESS:
            self._logger.log(f"    [Attachments] ### FAILED ### Failed to retrieve attachment list."
                             f" (HTTP status code : {res_status})")
            return False
        res_json = res.json()
        attachments = res_json.get("paginateResult").get("docs")
        if not attachments:
            return True

        # Save the list as JSON
        attachments_count = len(attachments)
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, NAME_ATTACHMENTS_JSON)
        if (save_path := self._auto_normalize_dir(save_path)) is None:
            return False
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(res_json, f, indent=2, ensure_ascii=False)
            self._logger.log(f"    [Attachments] {attachments_count} items", to_terminal=False)

        # Export each file
        answer = True
        for att in attachments:
            att_id = att.get("_id")
            original_name = att.get("originalName")
            file_path_proxied = att.get("filePathProxied")

            if not att_id or not file_path_proxied or not original_name:
                continue

            # Convert to "filename_id.ext" format
            stem, ext = os.path.splitext(original_name)
            save_file_name = f"{NAME_ATTACHMENT}_{att_id}_{stem}{ext}"

            if file_path_proxied.startswith("https") or file_path_proxied.startswith("http"):
                file_export_url = file_path_proxied
            else:
                file_export_url = f"{self._growi_url}/{file_path_proxied.lstrip('/')}"

            file_res = self._session.get(
                file_export_url,
                stream=True
            )

            if file_res.status_code != HTTP_RES_SUCCESS:
                self._logger.log(f"        >> ### FAILED ### Export failed ({original_name}):"
                                 f" Status {file_res.status_code}")
                answer = False
                continue

            save_path = os.path.join(save_dir, save_file_name)
            if (save_path := self._auto_normalize_dir(save_path)) is None:
                answer = False
                continue

            with open(save_path, "wb") as f:
                for chunk in file_res.iter_content(chunk_size=8192):
                    f.write(chunk)

        return answer

    def _export_comments(self, page_id: str, save_dir: str) -> bool:
        """Export comments for the specified page."""
        res = self._session.get(
            f"{self._growi_url}/_api/comments.get",
            params={
                "page_id": page_id
            }
        )
        res_status = res.status_code
        if res_status != HTTP_RES_SUCCESS:
            self._logger.log(f"    [Comments] ### FAILED ### Failed to retrieve comments."
                             f" (HTTP status code : {res_status})")
            return False
        res_json = res.json()
        comments_count = len(res_json.get("comments", []))
        if comments_count <= 0:
            return True

        # Save as JSON
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, NAME_COMMENTS_JSON)
        if (save_path := self._auto_normalize_dir(save_path)) is None:
            return False
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(res_json, f, indent=2, ensure_ascii=False)

        comments_ok = res_json.get("ok", False)
        if comments_ok:
            self._logger.log(f"    [Comments] {comments_count} items", to_terminal=False)
        else:
            error_reason = res_json.get("error", "?")
            self._logger.log(f"    [Comments] ### FAILED ### Failed to retrieve comments. ({error_reason})")

        return True

    def _export_tags(self, page_id: str, save_dir: str) -> bool:
        """Export tags for the specified page."""
        res = self._session.get(
            f"{self._growi_url}/_api/pages.getPageTag",
            params={
                "pageId": page_id
            }
        )
        res_status = res.status_code
        if res_status != HTTP_RES_SUCCESS:
            self._logger.log(f"    [Tags] ### FAILED ### Failed to retrieve tags. (HTTP status code : {res_status})")
            return False
        res_json = res.json()
        tags_count = len(res_json.get("tags", []))
        if tags_count <= 0:
            return True

        # Save as JSON
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, NAME_TAGS_JSON)
        if (save_path := self._auto_normalize_dir(save_path)) is None:
            return False
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(res_json, f, indent=2, ensure_ascii=False)

        tags_ok = res_json.get("ok", False)
        if tags_ok:
            self._logger.log(f"    [Tags] {tags_count} items", to_terminal=False)
        else:
            error_reason = res_json.get("error", "?")
            self._logger.log(f"    [Tags] ### FAILED ### Failed to retrieve tags. ({error_reason})")
            return False

        return True

    def _export_bookmark(self, page_id: str, save_dir: str) -> bool:
        """Export bookmarks for the specified page."""
        res = self._session.get(
            f"{self._growi_url}/_api/v3/bookmarks/info",
            params={
                "pageId": page_id
            }
        )
        res_status = res.status_code
        if res_status != HTTP_RES_SUCCESS:
            self._logger.log(f"    [Bookmarks] ### FAILED ### Failed to retrieve bookmarks."
                             f" (HTTP status code : {res_status})")
            return False
        res_json = res.json()
        bookmarks_count = res_json.get("sumOfBookmarks", 0)
        if bookmarks_count <= 0:
            return True

        # Save as JSON
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, NAME_BOOKMARKS_JSON)
        if (save_path := self._auto_normalize_dir(save_path)) is None:
            return False
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(res_json, f, indent=2, ensure_ascii=False)

        self._logger.log(f"    [Bookmarks] {bookmarks_count} items", to_terminal=False)
        return True

    def _export_markdown(self, page_id: str, save_dir: str) -> bool:
        """[Currently Unused] Export Markdown for the specified page."""
        res = self._session.get(
            f"{self._growi_url}/_api/v3/page/export/{page_id}",
            stream=True
        )

        res_status = res.status_code
        if res_status != HTTP_RES_SUCCESS:
            self._logger.log(f"    [Markdown] ### FAILED ### Failed to retrieve Markdown."
                             f" (HTTP status code : {res_status})")
            return False

        save_path = os.path.join(save_dir, NAME_MARKDOWN)
        if (save_path := self._auto_normalize_dir(save_path)) is None:
            return False
        with open(save_path, "wb") as f:
            for chunk in res.iter_content(chunk_size=8192):
                f.write(chunk)

        self._logger.log(f"    [Markdown] {NAME_MARKDOWN}", to_terminal=False)
        return True

    def _normalize_dir(self, path_str: str, normalization_form: str) -> Optional[str]:
        """Normalizes path."""
        normalized = path_str.strip()
        form = normalization_form.upper()
        if form == "NFC":
            normalized = unicodedata.normalize("NFC", normalized)
        elif form == "NFD":
            normalized = unicodedata.normalize("NFD", normalized)
        elif form == "NFKC":
            normalized = unicodedata.normalize("NFKC", normalized)
        elif form == "NFKD":
            normalized = unicodedata.normalize("NFKD", normalized)

        # for macOS, Linux
        drive_letter = ""
        remaining = normalized
        if self._os_name.lower() == "windows":
            # for Windows
            drive_letter = normalized[:2]
            remaining = normalized[2:]

        cleaned = drive_letter + re.sub(r'[:*?"<>|\t\n\r]', "_", remaining)
        if len(cleaned) >= self._pc_max_path_len:
            self._logger.log(f"### FAILED ### Path length exceeds the limit"
                             f" ({self._pc_max_path_len}): {cleaned}", to_terminal=False)
            return None

        return cleaned

    def _auto_normalize_dir(self, path_str: str) -> Optional[str]:
        return self._normalize_dir(path_str, self._normalization_form)

    def _check_connection(self) -> bool:
        """Checks connection to GROWI_URL."""
        self._logger.log(f"Checking connection to {self._growi_url}...")

        normalized_paths = {
            "raw": self._growi_path,
            "NFC": self._normalize_dir(self._growi_path, "NFC"),
            "NFD": self._normalize_dir(self._growi_path, "NFD")
        }

        for key, p in normalized_paths.items():
            path = p if p is not None else ""
            try:
                res = self._session.get(
                    f"{self._growi_url}/_api/v3/page",
                    params={
                        "path": path
                    },
                    timeout=DEFAULT_TIMEOUT
                )
                if res.status_code == HTTP_RES_SUCCESS:
                    res_json = res.json()
                    if "page" in res_json:
                        self._logger.log(f"    >> [SUCCESS] Page path '{path}' ({key}) verified successfully.")
                        self._growi_path = path
                        return True

                self._logger.log(
                    f"    >> ### FAILED ### Status {res.status_code}: Page path '{path}' ({key}) "
                    "does not exist on target GROWI.",
                    to_terminal=False
                )

            except requests.RequestException as e:
                self._logger.log(f"    >> ### ERROR ### Network exception: {e}")

        return False

    def _configuration_logging(self) -> None:
        access_token_warning = "### WARNING ### No ACCESS_TOKEN provided."
        if self._access_token:
            access_token_warning = "provided."

        self._logger.log(
            f"========================================\n"
            f" Configuration\n"
            f"----------------------------------------\n"
            f"GROWI_URL  = {self._growi_url}\n"
            f"GROWI_PATH = {self._growi_path}\n"
            f"EXPORT_DIR = {self._export_dir}\n"
            f"ACCESS_TOKEN = {access_token_warning}\n"
            f"NORMALIZATION_FORM = {self._normalization_form}\n"
            f"----------------------------------------\n"
            f"{self._os_name} Limitation: max path length = {self._pc_max_path_len}\n"
            f"========================================"
        )

    def run(self):
        """Executes the export process for all pages under the specified path."""
        self._logger.log("=== GROWI Export process started ===")

        self._configuration_logging()
        os.makedirs(self._export_dir, exist_ok=True)

        if not self._check_connection():
            self._logger.log("### FAILED ### Connection check failed. Aborting process.")
            return

        self._logger.log(f"Searching under specified path... : {self._growi_path}")
        pages = self._get_pages_under_path(self._growi_path)
        if not pages:
            self._logger.log(f"[SEARCHING] ### FAILED ### No matching pages found. {self._growi_path}")
            return

        self._logger.log(f"Target: {len(pages)} pages\n")

        total_count = len(pages)
        failed_count = 0
        index = 0
        for page in pages:
            index += 1
            page_path = page['path']
            page_id = page['_id']
            save_dir = os.path.join(self._export_dir, page_path.lstrip("/"))
            if (save_dir := self._auto_normalize_dir(save_dir)) is None:
                failed_count += 1
                continue

            self._logger.log(f"Exporting... ( {index} / {total_count}, NG: {failed_count} ): "
                             f"path={page_path}", overwrite=True)

            answer = True
            answer &= self._export_page(page_id, save_dir)
            # answer &= self._export_markdown(page_id, save_dir)
            answer &= self._export_attachments(page_id, save_dir)
            answer &= self._export_comments(page_id, save_dir)
            answer &= self._export_tags(page_id, save_dir)
            answer &= self._export_bookmark(page_id, save_dir)
            if not answer:
                failed_count += 1

            time.sleep(API_SLEEP_INTERVAL)  # Reducing server load

        success_count = total_count - failed_count
        self._logger.log(
            f"========================================\n"
            f" Summary\n"
            f"----------------------------------------\n"
            f"  Total pages : {total_count} pages\n"
            f"  Successful  : {success_count} pages\n"
            f"  Failed      : {failed_count} pages\n"
            f"========================================\n"
            f"[i] For details, please refer to {self._logger.filepath.name}"
        )

        self._logger.log("=== GROWI Export process finished ===\n")


class TerminalFileLogger:
    """Manages terminal output (overwriting line) and file logging independently."""

    ERASE_TO_EOL = "\033[K"                                     # erase to end of line
    CLEAN_ANSI_REGEX = re.compile(r"\033\[[0-9;]*[a-zA-Z]")     # ANSI escape sequences

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.file_handle = filepath.open("w", encoding="utf-8")
        self.terminal = sys.stdout

    def __enter__(self) -> TerminalFileLogger:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    @staticmethod
    def _get_timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def log(self, message: str, to_terminal: bool = True, to_file: bool = True, overwrite: bool = False) -> None:
        if to_terminal and message:
            if overwrite:
                self.terminal.write(f"\r{self.ERASE_TO_EOL}")
                self.terminal.write(f"\r{message}")
            else:
                self.terminal.write(f"\n{message}")
            self.terminal.flush()

        if to_file:
            clean_msg = self.CLEAN_ANSI_REGEX.sub("", message).rstrip()
            if clean_msg:
                self.file_handle.write(f"[{self._get_timestamp()}] {clean_msg}\n")
                self.file_handle.flush()

    def close(self) -> None:
        if not self.file_handle.closed:
            self.file_handle.close()


def parse_arguments() -> argparse.Namespace:
    """Function to parse command-line arguments."""
    # Get the path of the currently executing script (e.g., /path/to/script.py)
    # Consider sys.argv[0] as a fallback when not running the script directly (e.g., interactive environment)
    script_path = Path(__file__) if "__file__" in globals() else Path(sys.argv[0])

    # Set the path with the extension changed to .ini as the default value (e.g., script.ini)
    default_config_path = script_path.with_suffix(".ini")

    parser = argparse.ArgumentParser(description="GROWI Markdown Export.")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=default_config_path,  # Specify the dynamically generated path as the default
        # help="Path to the configuration file (default: %(default)s)",
        help=f"Path to the configuration file (default: ./{default_config_path.name})",
    )

    return parser.parse_args()


def load_config(config_path: Path) -> configparser.ConfigParser:
    """Function to safely load a configuration file without sections."""
    if not config_path.exists():
        print(f"### FAILED ### Configuration file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    conf = configparser.ConfigParser()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Prepend a dummy section so it can be read without existing sections
        dummy_content = f"[root]\n{content}"
        conf.read_file(io.StringIO(dummy_content))

    except Exception as e:
        print(f"### ERROR ### Failed to load configuration file: {e}", file=sys.stderr)
        sys.exit(1)

    return conf


if __name__ == "__main__":
    # Load the configuration file
    args = parse_arguments()
    config = load_config(args.config)

    try:
        conf_url = config.get("root", "GROWI_URL")
        conf_path = config.get("root", "GROWI_PATH")
    except NoOptionError as err:
        print("### FAILED ### Required parameters (GROWI_URL, GROWI_PATH) are not set.")
        exit(1)

    conf_dir = config.get("root", "EXPORT_DIR", fallback="./growi_export")
    conf_token = config.get("root", "ACCESS_TOKEN", fallback="")
    conf_normalization_form = config.get("root", "NORMALIZATION_FORM", fallback="NFC")

    # Determine log file name
    script_name = Path(__file__).stem if "__file__" in globals() else "growi_export"
    now_str = datetime.now().strftime("%Y%m%d%H%M%S")
    log_file_path = Path(f"{script_name}_{now_str}.log")

    # Export
    with TerminalFileLogger(log_file_path) as my_logger:
        exporter = GrowiExport(
            conf_url, conf_path, conf_token, conf_dir, my_logger, conf_normalization_form
        )
        exporter.run()
