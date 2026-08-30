"""Rulebook loading for the deterministic checker.

Loads manifest + the four rule files, resolves every `@<file>.<key>` data
reference against data/lexicons.json, data/patterns.json, and
data/state_apr_caps.json (a dangling reference raises), and exposes the rules
as validated `RulebookEntry` models with fully-resolved parameters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from backend.contracts import CheckKind, Product, RulebookEntry

_DATA_FILES = {
    "lexicons": "data/lexicons.json",
    "patterns": "data/patterns.json",
    "state_apr_caps": "data/state_apr_caps.json",
}


class RulebookLoadError(ValueError):
    """Raised on structural problems: missing files, dangling @refs."""


def _resolve_refs(value, data: dict[str, dict], where: str):
    """Recursively resolve '@file.key' strings inside parameters."""
    if isinstance(value, str) and value.startswith("@"):
        ref = value[1:]
        file_key, _, data_key = ref.partition(".")
        if file_key not in data or data_key not in data[file_key]:
            raise RulebookLoadError(f"dangling data reference '{value}' in {where}")
        return data[file_key][data_key]
    if isinstance(value, list):
        return [_resolve_refs(v, data, where) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_refs(v, data, where) for k, v in value.items()}
    return value


@dataclass
class Rulebook:
    version: str
    entries: list[RulebookEntry] = field(default_factory=list)

    @property
    def deterministic_rules(self) -> list[RulebookEntry]:
        return [r for r in self.entries if r.check_kind == CheckKind.DETERMINISTIC]

    @property
    def llm_judged_rules(self) -> list[RulebookEntry]:
        return [r for r in self.entries if r.check_kind == CheckKind.LLM_JUDGED]

    def for_product(self, product: Product, kind: CheckKind | None = None) -> list[RulebookEntry]:
        rules = [r for r in self.entries if r.product == product]
        if kind is not None:
            rules = [r for r in rules if r.check_kind == kind]
        return rules


def load_rulebook(rulebook_dir: str | Path) -> Rulebook:
    """Load and materialize the rulebook with all data references resolved."""
    root = Path(rulebook_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise RulebookLoadError(f"no manifest.json in {root}")
    manifest = json.loads(manifest_path.read_text())
    version = manifest.get("rulebook_version")
    if not version:
        raise RulebookLoadError("manifest.json missing rulebook_version")

    data: dict[str, dict] = {}
    for key, rel in _DATA_FILES.items():
        p = root / rel
        if not p.exists():
            raise RulebookLoadError(f"missing data file {rel}")
        loaded = json.loads(p.read_text())
        data[key] = {k: v for k, v in loaded.items() if not k.startswith("_")}

    entries: list[RulebookEntry] = []
    for rel in manifest.get("rule_files", []):
        p = root / rel
        if not p.exists():
            raise RulebookLoadError(f"missing rule file {rel}")
        doc = json.loads(p.read_text())
        for raw in doc.get("rules", []):
            raw = dict(raw)
            raw["parameters"] = _resolve_refs(
                raw.get("parameters", {}), data, f"{rel}:{raw.get('rule_id', '?')}"
            )
            entries.append(RulebookEntry.model_validate(raw))

    seen: set[str] = set()
    for r in entries:
        if r.rule_id in seen:
            raise RulebookLoadError(f"duplicate rule_id {r.rule_id}")
        seen.add(r.rule_id)

    return Rulebook(version=version, entries=entries)
