"""Canonical job listing dataclass shared across all scrapers."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class JobListing:
    source: str
    title: str
    company: str
    url: str
    apply_url: str
    description: str
    skills: list[str] = field(default_factory=list)
    location: str = ""
    salary_raw: str = ""
    posted_at: datetime = field(default_factory=datetime.utcnow)

    # Computed on post-init
    id: str = field(init=False)

    def __post_init__(self) -> None:
        self.id = self._fingerprint()

    def _fingerprint(self) -> str:
        """Stable SHA-256 hash; same job re-posted yields the same ID."""
        raw = "|".join([
            self.source,
            self.company.lower().strip(),
            self.title.lower().strip(),
            self.posted_at.date().isoformat() if self.posted_at else "",
        ])
        return hashlib.sha256(raw.encode()).hexdigest()

    def to_db_dict(self) -> dict:
        import json
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "company": self.company,
            "url": self.url,
            "apply_url": self.apply_url,
            "description": self.description,
            "skills": json.dumps(self.skills),
            "location": self.location,
            "salary_raw": self.salary_raw,
            "posted_at": self.posted_at.isoformat() if self.posted_at else "",
        }
