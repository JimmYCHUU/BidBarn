from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Config:
    dry_run: bool = True
    project_status: str = "testing"  # live placement needs exactly "The project is finished"
    sheet_check: str = "none"  # none | openai
    model: str = "gpt-5"
    model_reasoning_effort: str | None = "medium"
    max_photos_to_model: int = 12
    max_rules_age_hours: float = 12
    cache_hours: float = 12
    max_bids_per_run: int = 25
    open_details_only_for_candidates: bool = True
    pace_seconds: tuple[float, float] = (1.5, 4.0)
    profile_dir: Path = ROOT / "data/profile"
    db_path: Path = ROOT / "data/bot.sqlite"
    evidence_dir: Path = ROOT / "data/evidence"
    snapshot_dir: Path = ROOT / "rules/snapshots"
    override_dir: Path = ROOT / "rules/overrides"
    carbarn: dict = field(default_factory=dict)
    nichibo: dict = field(default_factory=dict)
    review: dict = field(default_factory=dict)

    PROJECT_FINISHED = "The project is finished"

    @property
    def project_finished(self) -> bool:
        return (self.project_status or "").strip() == self.PROJECT_FINISHED

    def placement_mode(self, live_flag: bool) -> tuple[bool, str]:
        """(dry_run, reason). Live only when the testing lock is off, dry_run is false and --live was given."""
        if not self.project_finished:
            return True, f"testing lock: config.yaml project_status is not '{self.PROJECT_FINISHED}'"
        if self.dry_run:
            return True, "dry_run is true in config.yaml"
        if not live_flag:
            return True, "--live flag not given"
        return False, "live placement enabled"

    @property
    def openai_api_key(self) -> str | None:
        return os.environ.get("OPENAI_API_KEY")


_PATH_KEYS = ("profile_dir", "db_path", "evidence_dir", "snapshot_dir", "override_dir")


def cache_dir(cfg: "Config") -> Path:
    d = cfg.db_path.parent / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_config(path: Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    path = path or ROOT / "config.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config()
    for key, value in raw.items():
        if not hasattr(cfg, key):
            continue
        if key in _PATH_KEYS:
            value = (ROOT / value).resolve()
        if key == "pace_seconds":
            value = (float(value[0]), float(value[1]))
        setattr(cfg, key, value)
    for d in (cfg.profile_dir, cfg.evidence_dir, cfg.snapshot_dir, cfg.override_dir, cfg.db_path.parent):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
