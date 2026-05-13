"""Google Drive client for the PDF source type.

PDFs live in Drive (not git, not vault). The pipeline lists them in an
"inbox" folder, downloads each to a temp file, parses it, and on
successful commit moves the original to a "processed" folder.

Auth uses a service account JSON key — works headless in CI. The folders
referenced by env vars must be shared with the service account's email
as Editor (a personal-Drive root folder owned by the SA cannot be used:
service accounts have no quota and cannot own files outside a Shared
Drive).
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path

from second_brain.config import gdrive_credentials_json

logger = logging.getLogger(__name__)

DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive",)
PDF_MIME = "application/pdf"
LIST_PAGE_SIZE = 200
DOWNLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MiB


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    modified_time: str
    web_view_link: str


def _service():
    """Build a Drive v3 client. Raises if credentials are misconfigured."""
    creds_path = gdrive_credentials_json()
    if not creds_path:
        raise RuntimeError("GDRIVE_CREDENTIALS_JSON not set — cannot authenticate to Drive")
    try:
        from google.oauth2 import service_account  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except ImportError as exc:
        raise RuntimeError("google-api-python-client / google-auth not installed") from exc
    creds = service_account.Credentials.from_service_account_file(
        creds_path, scopes=list(DRIVE_SCOPES)
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_pdfs(folder_id: str) -> list[DriveFile]:
    """List every PDF (non-trashed) directly inside ``folder_id``.

    Returns an empty list on API failure rather than raising — a Drive
    outage should never block local sources from being processed.
    """
    try:
        svc = _service()
        files: list[DriveFile] = []
        page_token: str | None = None
        query = f"'{folder_id}' in parents and mimeType='{PDF_MIME}' and trashed=false"
        while True:
            resp = (
                svc.files()
                .list(
                    q=query,
                    fields="nextPageToken, files(id,name,modifiedTime,webViewLink)",
                    pageSize=LIST_PAGE_SIZE,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for f in resp.get("files", []):
                files.append(
                    DriveFile(
                        id=f["id"],
                        name=f.get("name", f["id"]),
                        modified_time=f.get("modifiedTime", ""),
                        web_view_link=f.get(
                            "webViewLink",
                            f"https://drive.google.com/file/d/{f['id']}/view",
                        ),
                    )
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return files
    except Exception as exc:
        logger.error("Drive list_pdfs failed for folder %s: %s", folder_id, exc)
        return []


def download_pdf(file_id: str, dest: Path) -> Path | None:
    """Stream a PDF to ``dest``. Returns the path on success, else None."""
    try:
        from googleapiclient.http import MediaIoBaseDownload  # type: ignore

        svc = _service()
        request = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with io.FileIO(dest, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=DOWNLOAD_CHUNK_BYTES)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return dest
    except Exception as exc:
        logger.error("Drive download_pdf failed for %s: %s", file_id, exc)
        return None


def move_to_processed(file_id: str, source_folder_id: str, target_folder_id: str) -> bool:
    """Reparent ``file_id`` from inbox to processed. Best-effort."""
    try:
        svc = _service()
        svc.files().update(
            fileId=file_id,
            addParents=target_folder_id,
            removeParents=source_folder_id,
            fields="id, parents",
            supportsAllDrives=True,
        ).execute()
        return True
    except Exception as exc:
        logger.warning("Drive move_to_processed failed for %s: %s", file_id, exc)
        return False
