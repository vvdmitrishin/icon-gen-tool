"""Reference Compare data (spec §6.8.1, §10.1 ArtworkReferenceMatch).

Pairs each generated card (current version) with a style/category reference. The match is stored per
card (auto or manual) so it survives a browser refresh; deleting a reference falls back to auto.
"""
from __future__ import annotations

from typing import Any

from .jobs import JobFailure, JobStore
from .reference_manager import ReferenceManager, is_valid_ref_id

AUTO_ORDER = ("category", "style", "composition")  # 'negative' refs are never auto-matched


def auto_match(category: int, metas: list[dict[str, Any]]) -> dict[str, Any] | None:
    for kind in AUTO_ORDER:
        want = f"category_{category}" if kind == "category" else kind
        hit = next((m for m in metas if m["ref_type"] == want), None)
        if hit:
            return hit
    return None


class ComparisonService:
    def __init__(self, store: JobStore, refs: ReferenceManager) -> None:
        self.store, self.refs = store, refs

    @staticmethod
    def _view(meta: dict[str, Any] | None, mode: str) -> dict[str, Any] | None:
        if meta is None:
            return None
        rid = meta["ref_id"]
        return {"ref_id": rid, "ref_type": meta["ref_type"], "original_name": meta["original_name"],
                "width": meta["width"], "height": meta["height"], "mode": mode,
                "image_url": f"/api/references/{rid}/image", "thumbnail_url": f"/api/references/{rid}/thumbnail"}

    def _resolve(self, card: dict[str, Any], metas: list[dict[str, Any]], stored: dict[str, Any] | None):
        by_id = {m["ref_id"]: m for m in metas}
        if stored and stored["selection_mode"] == "manual" and stored["ref_id"] in by_id:
            return by_id[stored["ref_id"]], "manual"
        return auto_match(card["category"], metas), "auto"

    def _require(self, job_id: str, card_id: str) -> dict[str, Any]:
        if self.store.get_job(job_id) is None:
            raise JobFailure(404, "not_found", "Job not found.")
        card = self.store.card(job_id, card_id)
        if card is None:
            raise JobFailure(404, "not_found", "Card not found.")
        return card

    def card(self, job_id: str, card_id: str) -> dict[str, Any]:
        card = self._require(job_id, card_id)
        metas = self.refs.list()
        ref, mode = self._resolve(card, metas, self.store.comparison_ref(card_id))
        versions = self.store.versions_for_job(job_id).get(card_id, [])
        suggested = auto_match(card["category"], metas)
        return {
            "card_id": card_id, "variant_id": card["variant_id"], "position": card["position"],
            "category": card["category"], "object_name": card["object_name"], "status": card["status"],
            "image_url": f"/api/artifacts/{card['artifact_id']}/image" if card["artifact_id"] else None,
            "versions": [{"version": v["version"], "image_url": f"/api/artifacts/{v['artifact_id']}/image",
                          "is_current": bool(v["is_current"]), "qa_status": v["qa_status"]} for v in versions],
            "reference": self._view(ref, mode),
            "candidates": [{**self._view(m, "candidate"), "suggested": bool(suggested and suggested["ref_id"] == m["ref_id"])}
                           for m in metas],
            "message": None if ref else "No reference image is available; upload one in section 3 (References).",
        }

    def set_reference(self, job_id: str, card_id: str, ref_id: str | None) -> dict[str, Any]:
        card = self._require(job_id, card_id)
        if ref_id is None:
            self.store.clear_comparison_ref(card_id)  # back to automatic matching
        else:
            if not is_valid_ref_id(ref_id) or self.refs.get(ref_id) is None:
                raise JobFailure(404, "not_found", "Reference not found.")
            self.store.set_comparison_ref(card_id, ref_id, "manual")
        _ = card
        return self.card(job_id, card_id)

    def grid(self, job_id: str) -> dict[str, Any]:
        if self.store.get_job(job_id) is None:
            raise JobFailure(404, "not_found", "Job not found.")
        metas = self.refs.list()
        stored = self.store.comparison_refs_for_job(job_id)
        versions = self.store.versions_for_job(job_id)
        pairs = []
        for c in self.store.cards(job_id):
            ref, mode = self._resolve(c, metas, stored.get(c["card_id"]))
            cur = next((v for v in versions.get(c["card_id"], []) if v["is_current"]), None)
            pairs.append({
                "card_id": c["card_id"], "variant_id": c["variant_id"], "position": c["position"],
                "category": c["category"], "object_name": c["object_name"], "status": c["status"],
                "image_url": f"/api/artifacts/{c['artifact_id']}/image" if c["artifact_id"] else None,
                "qa_status": cur["qa_status"] if cur else "not_run", "art_review": cur["art_review"] if cur else None,
                "reference": self._view(ref, mode)})
        return {"job_id": job_id, "pairs": pairs, "reference_count": len(metas)}
