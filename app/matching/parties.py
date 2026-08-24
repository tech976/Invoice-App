"""Resolve a party read off a bill to a row in `parties`.

The same firm is spelled a dozen ways across vendors — 'SUNRISE TRADERS
-Karnataka', 'Sunrise Traders', 'SUNRISE TRDRS'. Matching runs strongest-signal
first:

  1. GSTIN            - an exact, checksummed legal identifier
  2. known alias      - a spelling we have already resolved once
  3. fuzzy name       - same state, high similarity
  4. create           - a genuinely new party

Every spelling seen gets recorded as an alias, so step 3 is needed less often
as the ledger fills up.
"""
from __future__ import annotations

import logging

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction.normalize import (
    clean_gstin,
    clean_pan,
    clean_text,
    normalize_name,
    normalize_name_light,
    pan_from_gstin,
    state_from_gstin,
)
from app.models import Party, PartyAlias
from app.schemas import ExtractedParty

log = logging.getLogger(__name__)

# Below this, a name match is too weak to merge two firms automatically.
FUZZY_THRESHOLD = 88


def _tight(name: str) -> str:
    """'lcdf e 46' -> 'lcdfe46', so spacing differences stop mattering."""
    return name.replace(" ", "")


def _fuzzy_score(name_a: str, name_b: str) -> float:
    """Score two raw party names, 0-100.

    Two independent readings must agree, and the lower one wins:

    * the *aggressive* key drops 'Pvt Ltd', 'Enterprises', 'Traders' so that
      'Riverstone Impex Private Limited B-12' and 'RIVERSTONE IMPEX PVT LTD B12' collapse
      onto each other;
    * the *light* key keeps those words, because they are exactly what tells
      'Sunrise Traders' apart from 'Shan Trading Co'.

    Taking the minimum means a pair has to look like the same firm under both
    readings. Merging two different companies silently corrupts every
    downstream ledger, so this errs towards creating a duplicate party — which
    a human can merge — over fusing two real ones, which is unrecoverable.
    """
    hard_a, hard_b = normalize_name(name_a), normalize_name(name_b)
    soft_a, soft_b = normalize_name_light(name_a), normalize_name_light(name_b)

    hard = max(
        fuzz.token_set_ratio(hard_a, hard_b),
        fuzz.ratio(_tight(hard_a), _tight(hard_b)),
    )
    soft = max(
        fuzz.token_set_ratio(soft_a, soft_b),
        fuzz.ratio(_tight(soft_a), _tight(soft_b)),
    )
    return min(hard, soft)


def find_party(
    db: Session,
    *,
    name: str | None,
    gstin: str | None = None,
    state_code: str | None = None,
) -> tuple[Party | None, str]:
    """Look up a party. Returns (party, how_it_matched)."""
    gstin = clean_gstin(gstin)
    if gstin:
        hit = db.scalar(select(Party).where(Party.gstin == gstin))
        if hit:
            return hit, "gstin"

    normalized = normalize_name(name)
    if not normalized:
        return None, "none"

    alias = db.scalar(
        select(PartyAlias).where(PartyAlias.normalized_alias == normalized)
    )
    if alias:
        return db.get(Party, alias.party_id), "alias"

    exact = db.scalars(select(Party).where(Party.normalized_name == normalized)).all()
    if len(exact) == 1:
        return exact[0], "name"
    if len(exact) > 1 and state_code:
        same_state = [p for p in exact if p.state_code == state_code]
        if len(same_state) == 1:
            return same_state[0], "name+state"

    # Fuzzy, restricted to the same state when we know it — two firms with
    # similar names in different states are usually genuinely different.
    stmt = select(Party)
    if state_code:
        stmt = stmt.where((Party.state_code == state_code) | (Party.state_code.is_(None)))
    candidates = db.scalars(stmt).all()
    if not candidates:
        return None, "none"

    best, best_score = None, 0.0
    for cand in candidates:
        score = _fuzzy_score(name or "", cand.display_name or cand.legal_name)
        if score > best_score:
            best, best_score = cand, score

    if best is not None and best_score >= FUZZY_THRESHOLD:
        # A GSTIN on both sides that disagrees is decisive: not the same firm.
        if gstin and best.gstin and best.gstin != gstin:
            return None, "none"
        log.info("fuzzy-matched %r to party %s (score %.0f)", name, best.id, best_score)
        return best, f"fuzzy:{best_score:.0f}"

    return None, "none"


def resolve_party(
    db: Session,
    extracted: ExtractedParty | None,
    *,
    role: str | None = None,
) -> Party | None:
    """Find or create the party, enrich it, and remember the spelling used."""
    if extracted is None:
        return None

    name = clean_text(extracted.name)
    gstin = clean_gstin(extracted.gstin)
    if not name and not gstin:
        return None

    state_code = extracted.state_code
    gstin_state, gstin_state_name = state_from_gstin(gstin)
    if gstin_state:
        # The GSTIN prefix is authoritative over a printed state code.
        state_code = gstin_state

    party, how = find_party(db, name=name, gstin=gstin, state_code=state_code)

    if party is None:
        party = Party(
            gstin=gstin,
            pan=clean_pan(extracted.pan) or pan_from_gstin(gstin),
            fssai=clean_text(extracted.fssai),
            legal_name=name or gstin or "Unknown",
            normalized_name=normalize_name(name or gstin),
            display_name=name,
            address=clean_text(extracted.address),
            city=clean_text(extracted.city),
            state_name=clean_text(extracted.state_name) or gstin_state_name,
            state_code=state_code,
            pincode=clean_text(extracted.pincode),
            phone=clean_text(extracted.phone),
            email=clean_text(extracted.email),
        )
        db.add(party)
        db.flush()
        log.info("created party %s %r (%s)", party.id, party.legal_name, gstin or "no GSTIN")
    else:
        _enrich(party, extracted, gstin, state_code, gstin_state_name)

    if role:
        setattr(party, f"is_{role}", True)

    _record_alias(db, party, name)
    return party


def _enrich(party: Party, extracted: ExtractedParty, gstin, state_code, gstin_state_name) -> None:
    """Fill blanks from this bill without overwriting what we already know.

    A later bill may carry the GSTIN a first one omitted. It must not
    overwrite an address a human has corrected, so only empty fields move.
    """
    if gstin and not party.gstin:
        party.gstin = gstin
    if not party.pan:
        party.pan = clean_pan(extracted.pan) or pan_from_gstin(gstin)
    for field, value in (
        ("fssai", clean_text(extracted.fssai)),
        ("address", clean_text(extracted.address)),
        ("city", clean_text(extracted.city)),
        ("state_name", clean_text(extracted.state_name) or gstin_state_name),
        ("state_code", state_code),
        ("pincode", clean_text(extracted.pincode)),
        ("phone", clean_text(extracted.phone)),
        ("email", clean_text(extracted.email)),
    ):
        if value and not getattr(party, field):
            setattr(party, field, value)


def _record_alias(db: Session, party: Party, name: str | None) -> None:
    normalized = normalize_name(name)
    if not normalized or normalized == party.normalized_name:
        return

    # Check the loaded collection, not a fresh SELECT. Buyer and consignee are
    # frequently the same firm under the same spelling, and both resolve
    # before anything is flushed — a query would miss the pending row and the
    # second insert would collide on the alias uniqueness constraint.
    for alias in party.aliases:
        if alias.normalized_alias == normalized:
            # `or 0` because the column default only lands at flush time, and
            # this alias may have been appended moments ago in the same unit
            # of work.
            alias.seen_count = (alias.seen_count or 0) + 1
            return

    party.aliases.append(PartyAlias(alias=name, normalized_alias=normalized))


def resolve_broker(db: Session, broker_name: str | None) -> Party | None:
    """Brokers appear as a bare name — no GSTIN, no address."""
    name = clean_text(broker_name)
    if not name:
        return None

    party, _ = find_party(db, name=name)
    if party is None:
        party = Party(
            legal_name=name,
            normalized_name=normalize_name(name),
            display_name=name,
            is_broker=True,
        )
        db.add(party)
        db.flush()
        log.info("created broker %s %r", party.id, name)
    else:
        party.is_broker = True
        _record_alias(db, party, name)
    return party


def resolve_transporter(
    db: Session, *, name: str | None, transporter_id: str | None
) -> Party | None:
    """Transporters are identified by a GSTIN or a 15-char TRANSIN."""
    name = clean_text(name)
    tid = clean_gstin(transporter_id)
    if not name and not tid:
        return None

    state_code, state_name = state_from_gstin(tid)
    party, _ = find_party(db, name=name, gstin=tid, state_code=state_code)
    if party is None:
        party = Party(
            gstin=tid,
            legal_name=name or tid,
            normalized_name=normalize_name(name or tid),
            display_name=name,
            state_code=state_code,
            state_name=state_name,
            is_transporter=True,
        )
        db.add(party)
        db.flush()
        log.info("created transporter %s %r", party.id, party.legal_name)
    else:
        party.is_transporter = True
        if tid and not party.gstin:
            party.gstin = tid
        _record_alias(db, party, name)
    return party
