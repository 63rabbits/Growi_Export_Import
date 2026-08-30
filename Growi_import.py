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
import mimetypes

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
HTTP_RES_CREATED        = 201

NAME_PAGE_JSON          = "page.json"
NAME_ATTACHMENTS_JSON   = "attachments.json"
NAME_COMMENTS_JSON      = "comments.json"
NAME_TAGS_JSON          = "tags.json"
NAME_BOOKMARKS_JSON     = "bookmarks.json"
NAME_MARKDOWN           = "markdown.md"
NAME_ATTACHMENT         = "attachment"

STANDARD_BODY = "\n:notebook: Contents\n\n$lsx(depth=1)\n"
GROWI_GRANT_PUBLIC      = 1
GROWI_GRANT_RESTRICTED  = 2
GROWI_GRANT_SPECIFIED   = 3
GROWI_GRANT_OWNER       = 4
GROWI_GRANT_USER_GROUP  = 5


class GrowiImport:

    def __init__(self, growi_url: str, growi_path: str, access_token: str, export_dir: str, logger: TerminalFileLogger,
                 normalization_form: str, comments: bool, bookmark: bool):
        self._os_name = self._get_os_type()
        self._pc_max_path_len = self._get_max_path_len()

        self._growi_url = growi_url.rstrip("/")
        self._growi_path = growi_path.rstrip("/")
        self._access_token = access_token
        self._export_dir = str(Path(export_dir).absolute())
        self._logger = logger
        self._normalization_form = normalization_form
        self._upload_comments = comments
        self._upload_bookmark = bookmark
        self._session = self._create_session()
        self._page_id_map = {}
        self._attachment_id_replacer = MarkdownTextReplacer()

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

    @staticmethod
    def _get_pages_under_dir(export_dir: str) -> list:
        """Retrieves a list of pages under the specified directory."""
        path = Path(export_dir).absolute()
        target_pages = [str(p) for p in path.rglob('**')]
        target_pages.sort()
        return target_pages

    def _import_dummy_page_and_tags(self, page_dir: str, growi_path: str) -> Optional[dict]:
        """Import the dummy page with tags."""
        growi_body = STANDARD_BODY
        growi_grant = GROWI_GRANT_PUBLIC
        growi_wip = False
        growi_tags = []

        # Read page.json
        path = Path(page_dir) / NAME_PAGE_JSON
        if path.exists():
            json_data = json.loads(path.read_text(encoding="utf-8"))
            # growi_body = json_data.get("page", {}).get("revision", {}).get("body")
            growi_grant = json_data.get("page", {}).get("grant", GROWI_GRANT_PUBLIC)
            # growi_wip = json_data.get("page", {}).get("wip", False)

        # Read tags.json
        path = Path(page_dir) / NAME_TAGS_JSON
        if path.exists():
            json_data = json.loads(path.read_text(encoding="utf-8"))
            growi_tags = json_data.get("tags")

        # Import
        res = self._session.post(
            f"{self._growi_url}/_api/v3/page",
            json={
                "body": growi_body,
                "path": growi_path,
                "grant": growi_grant,
                "wip": growi_wip,
                "pageTags": growi_tags
            },
            timeout=DEFAULT_TIMEOUT
        )
        res_status = res.status_code
        if res_status != HTTP_RES_CREATED:
            self._logger.log(f"    [Page] ### FAILED ### Failed to import dummy page with tags. "
                             f"(HTTP status code : {res_status})", to_terminal=False)
            return None

        page_json = res.json()
        return page_json

    def _update_page(self, page_dir: str, page_info: dict) -> bool:
        """Update the specified page."""

        # Read page.json
        path = Path(page_dir) / NAME_PAGE_JSON
        if not path.exists():
            return True

        json_data = json.loads(path.read_text(encoding="utf-8"))
        growi_body = json_data.get("page").get("revision", {}).get("body")
        growi_page_id = page_info.get("page", {}).get("_id")
        growi_revision_id = page_info.get("revision", {}).get("_id")
        growi_grant = json_data.get("page", {}).get("grant", GROWI_GRANT_PUBLIC)
        growi_wip = json_data.get("page", {}).get("wip", False)

        # Replace attachment-ID
        for key, value in self._page_id_map.items():
            old_word = f"/attachment/{key}"
            new_word = f"/attachment/{value}"
            growi_body = self._attachment_id_replacer.replace(growi_body, target=old_word, replacement=new_word)

        # Update
        res = self._session.put(
            f"{self._growi_url}/_api/v3/page",
            json={
                "body": growi_body,
                "pageId": growi_page_id,
                "revisionId": growi_revision_id,
                "grant": growi_grant,
                "wip": growi_wip
            },
            timeout=DEFAULT_TIMEOUT
        )
        res_status = res.status_code
        if res_status != HTTP_RES_CREATED:
            self._logger.log(f"    [Page] ### FAILED ### Failed to import. (HTTP status code : {res_status})",
                             to_terminal=False)
            return False

        # noinspection PyUnusedLocal
        page_json = res.json()

        return True

    def _import_attachments(self, page_dir: str, page_info: dict) -> bool:
        """Import attachments for the specified page."""
        path = Path(page_dir).absolute()
        attachments = [
            f for f in path.iterdir()
            if f.is_file() and f.name.startswith(f"{NAME_ATTACHMENT}_")
        ]
        total_count = len(attachments)
        if total_count <= 0:
            return True

        self._logger.log(f"    [Attachments] {total_count} items", to_terminal=False)

        page_id = page_info.get("page", {}).get("_id")
        success_count = 0
        for att in attachments:
            # attachment file name format : attachment_<id>_<filename>.<ext>
            split_name = att.name.split("_", 2)
            old_id = split_name[1]
            att_name = split_name[2]

            with att.open("rb") as f:
                mime_type, _ = mimetypes.guess_type(att)
                if not mime_type:
                    mime_type = "application/octet-stream"

                res = self._session.post(
                    f"{self._growi_url}/_api/v3/attachment",
                    data={"page_id": page_id},
                    files={"file": (att_name, f, mime_type)}
                )
                res_status = res.status_code
                if res_status != HTTP_RES_SUCCESS:
                    self._logger.log(f"    [Attachment] ### FAILED ### Failed to import {att.name}. "
                                     f"(HTTP status code : {res_status})",
                                     to_terminal=False)
                    continue

                res_json = res.json()
                new_id = res_json.get("attachment", {}).get("_id")
                self._page_id_map[old_id] = new_id

            success_count += 1

        return success_count == total_count

    def _import_comments(self, page_dir: str, page_info: dict) -> bool:
        """Import comments for the specified page."""
        # Read comments.json
        path = Path(page_dir) / NAME_COMMENTS_JSON
        if not path.exists():
            return True

        json_data = json.loads(path.read_text(encoding="utf-8"))
        comments = json_data.get("comments", [])

        # if not comments or not isinstance(comments, list):
        #     return True

        total_count = len(comments)
        if total_count <= 0:
            return True

        comments.sort(key=lambda d: d.get("createdAt") or "")
        comment_id_map = {}
        success_count = 0
        for c in comments:
            creator = c.get("creator", {})
            name = creator.get("name", "Unknown")
            username = creator.get("username", "Unknown")
            creator = f"{name}(@{username})"
            created_at = c.get("createdAt", "?")
            updated_at = c.get("updatedAt", "?")
            original_body = c.get("comment", "")
            comment_body = f"\n> [Imported] Author: **{creator}**"
            if created_at == updated_at:
                comment_body = comment_body + ", " + f"Create: {created_at}"
            else:
                comment_body = comment_body + ", " + f"Create: {created_at}, Update: {updated_at}"

            comment_body = comment_body + "\n\n" + f"{original_body}"

            # Replace attachment-ID
            for key, value in self._page_id_map.items():
                old_word = f"/attachment/{key}"
                new_word = f"/attachment/{value}"
                comment_body = self._attachment_id_replacer.replace(comment_body, target=old_word, replacement=new_word)

            payload = {
                "commentForm[page_id]": page_info.get("page", {}).get("_id"),
                "commentForm[revision_id]": page_info.get("revision", {}).get("_id"),
                "commentForm[comment]": comment_body
            }

            reply_to_id = c.get("replyTo")
            if reply_to_id:
                payload["commentForm[replyTo]"] = comment_id_map[reply_to_id]

            res = self._session.post(
                f"{self._growi_url}/_api/comments.add",
                data=payload
            )
            res_status = res.status_code
            if res_status in (HTTP_RES_SUCCESS, HTTP_RES_CREATED):
                res_json = res.json()
                if res_json.get("ok"):
                    success_count += 1
                    comment_data = res_json.get("comment", {})
                    old_id = c.get("_id")
                    new_id = comment_data.get("_id") or comment_data.get("id")
                    comment_id_map[old_id] = new_id
                else:
                    self._logger.log(f"    [Comments] ### FAILED ### Failed add comment. "
                                     f"(HTTP status code : {res_status})", to_terminal=False)
                    return False
            else:
                self._logger.log(f"    [Comments] Status {res.status_code}: {res.text}", to_terminal=False)
                return False

        self._logger.log(f"    [Comments] {success_count} items", to_terminal=False)

        return True

    def _import_bookmark(self, page_dir: str, page_info: dict) -> bool:
        """Import bookmark for the specified page."""
        # Read bookmark.json
        path = Path(page_dir) / NAME_BOOKMARKS_JSON
        if not path.exists():
            return True

        json_data = json.loads(path.read_text(encoding="utf-8"))
        count = (
                json_data.get("sumOfBookmarks", 0)
                or (1 if json_data.get("isBookmarked", False) else 0)
                or len(json_data.get("bookmarkedUsers", []))
        )
        if count <= 0:
            return True

        res = self._session.put(
            f"{self._growi_url}/_api/v3/bookmarks",
            json={
                "pageId": page_info.get("page", {}).get("id"),
                "bool": True
            }
        )
        res_status = res.status_code
        if res_status != HTTP_RES_SUCCESS:
            self._logger.log(f"    [Bookmark] ### FAILED ### Status {res_status}: {res.text}", to_terminal=False)
            return False

        self._logger.log("    [Bookmark] Added successfully", to_terminal=False)

        return True

    @staticmethod
    def _normalize_path(path_str: str, normalization_form: str) -> str:
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

        return normalized

    def _auto_normalize_path(self, path_str: str) -> str:
        return self._normalize_path(path_str, self._normalization_form)

    def _check_connection(self) -> bool:
        """Checks connection to GROWI_URL."""
        self._logger.log(f"Checking connection to {self._growi_url}...")

        normalized_paths = {
            "raw": self._growi_path,
            "NFC": self._normalize_path(self._growi_path, "NFC"),
            "NFD": self._normalize_path(self._growi_path, "NFD")
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
            f"IMPORT_COMMENTS = {self._upload_comments}\n"
            f"IMPORT_BOOKMARK = {self._upload_bookmark}\n"
            f"----------------------------------------\n"
            f"{self._os_name} Limitation: max path length = {self._pc_max_path_len}\n"
            f"========================================"
        )

    def run(self):
        """Executes the import process for all pages under the specified directory."""
        self._logger.log("=== GROWI Import process started ===")

        self._configuration_logging()
        if not os.path.isdir(self._export_dir):
            self._logger.log(f"### FAILED ### The specified directory does not exist: {self._export_dir}")
            return

        if not self._check_connection():
            self._logger.log("### FAILED ### Connection check failed. Aborting process.")
            return

        self._logger.log(f"Searching under specified directory... : {self._export_dir}")
        pages = self._get_pages_under_dir(self._export_dir)
        if not pages:
            self._logger.log(f"[SEARCHING] ### FAILED ### No matching pages found. {self._export_dir}")
            return

        self._logger.log(f"Target: {len(pages)} pages\n")

        total_count = len(pages)
        failed_count = 0
        index = 0
        for directory in pages:
            index += 1
            raw_growi_path = os.path.join(
                self._growi_path,
                directory.lstrip(str(Path(self._export_dir).parent))
            ).replace("\\", "/")

            self._logger.log(f"Importing... ( {index} / {total_count}, NG: {failed_count} ): "
                             f"raw path={raw_growi_path}", overwrite=True)

            growi_path = self._auto_normalize_path(raw_growi_path)
            page_info = self._import_dummy_page_and_tags(directory, growi_path)
            if not page_info:
                failed_count += 1
            else:
                self._page_id_map = {}
                answer = True
                answer &= self._import_attachments(directory, page_info)
                answer &= self._update_page(directory, page_info)
                if self._upload_comments:
                    answer &= self._import_comments(directory, page_info)
                if self._upload_bookmark:
                    answer &= self._import_bookmark(directory, page_info)
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


class MarkdownTextReplacer:
    """
    A class to replace specified strings in Markdown text
    while ignoring code blocks and inline code.
    """

    def __init__(self):
        # Pre-compile the regex pattern for identifying code elements
        self.pattern = re.compile(
            # 1. Indented code block
            r'(^[ \t]{4,}.*?(?:\r?\n|$))|'  
            # 2. Fenced code block (accounting for spaces/tabs before opening ```)
            r'([ \t]*```[\s\S]*?\n[ \t]*```[ \n$])|'  
            # 3. Inline code
            r'(`[^`\n]+`)',
            re.MULTILINE
        )

    def replace(self, markdown_text: str, target: str, replacement: str) -> str:
        """
        Replaces the target string with the replacement string in the Markdown text,
        excluding any code blocks or inline code.
        """
        parts = []
        last_end = 0

        for m in self.pattern.finditer(markdown_text):
            # Replace the target string in the normal text preceding the code block
            normal_text = markdown_text[last_end:m.start()]
            parts.append(normal_text.replace(target, replacement))

            # Append the matched code block as-is (without replacement)
            parts.append(m.group(0))
            last_end = m.end()

        # Process any remaining normal text at the end
        normal_text = markdown_text[last_end:]
        parts.append(normal_text.replace(target, replacement))

        return "".join(parts)


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

    parser = argparse.ArgumentParser(description="GROWI Markdown Import.")
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
    conf_import_comments = config.getboolean("root", "IMPORT_COMMENTS", fallback=True)
    conf_import_bookmark = config.getboolean("root", "IMPORT_BOOKMARK", fallback=True)

    # Determine log file name
    script_name = Path(__file__).stem if "__file__" in globals() else "growi_export"
    now_str = datetime.now().strftime("%Y%m%d%H%M%S")
    log_file_path = Path(f"{script_name}_{now_str}.log")

    # Export
    with TerminalFileLogger(log_file_path) as my_logger:
        importer = GrowiImport(
            conf_url, conf_path, conf_token, conf_dir, my_logger, conf_normalization_form,
            comments=conf_import_comments, bookmark=conf_import_bookmark
        )
        importer.run()
