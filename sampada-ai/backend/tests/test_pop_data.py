"""Package-of-practices (PoP) data path.

``crops.json`` now carries a per-crop ``pop`` block transcribed verbatim from
government/ICAR extension publications, cached under ``app/data/sources/pop_pdfs``
with line-referenced citations. The block is advisory data for clients (the app
UI and the voice assistant read it through ``GET /crops``); the scoring engine
deliberately does NOT read it - seasons already drive seasonal fit, and schema 2
removed sowing months precisely because they were hand-written. They return here
only as sourced, quoted, line-cited values.

The suite pins three contracts:

  1. Honesty of coverage - only crops whose agronomy was actually transcribed
     have a ``pop`` block; the others carry none, so a client can never mistake
     silence for "no recommendation".
  2. Verbatim sourcing - every PoP value cites a quote and a cached file that
     exists on disk, so a reviewer can check any number without leaving the repo.
  3. The soybean conflict resolution - NFSM records vertisols as where soybean
     performs best, which settles the ``local_review_needed`` entry that flagged
     black cotton soil scoring as merely tolerated under the EcoCrop envelope.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "data"
SOURCES_DIR = DATA_DIR / "sources"

TRANSCRIBED_CROPS = {"soybean", "cotton", "groundnut", "sunflower", "safflower"}


def _load_crops_payload() -> dict:
    return json.loads((DATA_DIR / "crops.json").read_text(encoding="utf-8"))


def _crop(payload: dict, crop_id: str) -> dict:
    return next(c for c in payload["crops"] if c["id"] == crop_id)


# ---------------------------------------------------------------------------
# 1. Coverage is explicit, not implied
# ---------------------------------------------------------------------------


def test_transcribed_crops_have_pop_block():
    payload = _load_crops_payload()
    for crop_id in TRANSCRIBED_CROPS:
        pop = _crop(payload, crop_id).get("pop")
        assert pop, f"{crop_id} was transcribed but carries no pop block"
        assert pop.get("sowing"), f"{crop_id}: sowing window missing"


def test_untranscribed_crops_carry_no_pop_block():
    payload = _load_crops_payload()
    ids = {c["id"] for c in payload["crops"]}
    untranscribed = ids - TRANSCRIBED_CROPS
    assert untranscribed, "crop table changed; re-check pop coverage"
    for crop_id in untranscribed:
        assert not _crop(payload, crop_id).get("pop"), (
            f"{crop_id} has a pop block but no transcribed source - that would be "
            "an invented number wearing a citation"
        )


# ---------------------------------------------------------------------------
# 2. Every value is verbatim-quoted and the citation exists on disk
# ---------------------------------------------------------------------------


def test_every_pop_quote_cites_an_existing_file():
    payload = _load_crops_payload()
    for crop in payload["crops"]:
        pop = crop.get("pop")
        if not pop:
            continue  # untranscribed crops carry no block at all - covered above
        quotes = pop.get("quotes") or []
        assert quotes, f"{crop['id']}: pop block must carry at least one verbatim quote"
        for quote in quotes:
            assert quote.get("text", "").strip(), f"{crop['id']}: empty quote text"
            cited = SOURCES_DIR / quote["file"]
            assert cited.exists(), f"{crop['id']}: cited file {quote['file']} is not in the repo"


def test_pop_source_file_declares_its_publications():
    payload = json.loads((SOURCES_DIR / "pop_transcribed.json").read_text(encoding="utf-8"))
    sources = payload["_meta"]["sources"]
    assert {"nfsm_oilseeds_2018", "dcd_cotton_ready_reckoner", "icar_iisr_soybean_2023"} <= set(sources)
    for source in sources.values():
        assert source.get("url"), "each publication needs its URL recorded"
        assert source.get("cached_file"), "each publication needs its cached file recorded"


# ---------------------------------------------------------------------------
# 3. The soybean vertisol conflict is settled by the local source
# ---------------------------------------------------------------------------


def test_soybean_black_soil_promoted_to_optimal():
    payload = _load_crops_payload()
    soybean = _crop(payload, "soybean")
    assert "BLACK" in soybean["soil_types_optimal"], (
        "NFSM: soybean performs best on vertisols; black cotton soil is a vertisol"
    )
    provenance = soybean["provenance"]["soil_types_optimal"]
    assert "package" in provenance["citation"].lower() or "pop" in str(provenance).lower()


def test_safflower_black_soil_optimal_per_reckoner():
    payload = _load_crops_payload()
    safflower = _crop(payload, "safflower")
    assert "BLACK" in safflower["soil_types_optimal"], (
        "NFSM: safflower is confined to rabi vertisols in Maharashtra"
    )


def test_old_soybean_conflict_entry_removed():
    payload = _load_crops_payload()
    conflicts = [
        entry
        for entry in payload["_meta"]["local_review_needed"]
        if entry["crop"] == "soybean" and entry["field"] == "soil_types_optimal"
    ]
    assert not conflicts, "the conflict was resolved by the PoP source; the flag must go"


# ---------------------------------------------------------------------------
# 4. Clients get the block through the API
# ---------------------------------------------------------------------------


def test_api_crops_expose_pop_with_citation(client: TestClient):
    response = client.get("/api/v1/crops")
    assert response.status_code == 200
    body = response.json()
    soybean = next(c for c in body["crops"] if c["id"] == "soybean")
    pop = soybean["pop"]
    assert pop["sowing"]["window"] == ["06-20", "07-05"], (
        "ICAR Central zone: 20th June - 5th July"
    )
    assert pop["sowing"]["window_basis"], "the window must say what its source published"
    assert pop["seed_rate_kg_ha"][0] <= pop["seed_rate_kg_ha"][1]
    assert pop["fertilizer"]["N"] > 0
    assert pop["quotes"], "the API must hand the verbatim quotes to the client too"


def test_api_provenance_lists_the_pop_sources(client: TestClient):
    response = client.get("/api/v1/crops")
    sources = response.json()["provenance"]["sources"]
    assert "package_of_practices" in sources
    assert sources["package_of_practices"]["verification"] == "VERBATIM_QUOTE"
