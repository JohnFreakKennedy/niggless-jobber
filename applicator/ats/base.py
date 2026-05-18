"""Abstract base class for ATS adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from parser.schema import JobListing


class ApplicationStatus(str, Enum):
    APPLIED = "applied"
    FAILED = "failed"
    SKIPPED = "skipped"
    MANUAL_REVIEW = "manual_review_needed"
    DRY_RUN = "dry_run"


@dataclass
class PersonalInfo:
    """Thin wrapper around personal_info.json loaded at engine startup."""
    raw: dict

    def get(self, *keys: str, default: str = "") -> str:
        val = self.raw
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k, default)
            else:
                return default
        return str(val) if val else default


@dataclass
class ApplicationResult:
    job_id: str
    status: ApplicationStatus
    reason: str | None = None
    applied_at: datetime | None = None
    cv_path: Path | None = None
    cover_letter_path: Path | None = None
    prompt_version_cv: str = ""
    prompt_version_cl: str = ""

    @classmethod
    def success(cls, job_id: str, cv_path: Path | None, cl_path: Path | None,
                pv_cv: str = "", pv_cl: str = "") -> "ApplicationResult":
        return cls(
            job_id=job_id,
            status=ApplicationStatus.APPLIED,
            applied_at=datetime.now(timezone.utc),
            cv_path=cv_path,
            cover_letter_path=cl_path,
            prompt_version_cv=pv_cv,
            prompt_version_cl=pv_cl,
        )

    @classmethod
    def failure(cls, job_id: str, reason: str) -> "ApplicationResult":
        return cls(job_id=job_id, status=ApplicationStatus.FAILED, reason=reason)

    @classmethod
    def skipped(cls, job_id: str, reason: str) -> "ApplicationResult":
        return cls(job_id=job_id, status=ApplicationStatus.SKIPPED, reason=reason)


class BaseATSAdapter(ABC):
    name: str = "base"

    @abstractmethod
    async def apply(
        self,
        page: "Page",
        listing: JobListing,
        info: PersonalInfo,
        cv_pdf: Path | None,
        cover_letter_pdf: Path | None,
    ) -> ApplicationResult:
        ...

    async def _upload_file(self, page: "Page", selector: str, path: Path) -> bool:
        try:
            file_input = await page.wait_for_selector(selector, state="attached", timeout=5_000)
            await file_input.set_input_files(str(path))
            return True
        except Exception:
            return False

    async def _click(self, page: "Page", selector: str, timeout: int = 10_000) -> bool:
        import asyncio, random
        try:
            el = await page.wait_for_selector(selector, timeout=timeout)
            await asyncio.sleep(random.uniform(0.3, 0.8))
            await el.click()
            return True
        except Exception:
            return False
