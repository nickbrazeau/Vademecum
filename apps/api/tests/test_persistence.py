"""Piles, items and flags survive, relate, and really delete."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from vademecum.db import apply_migrations, connect
from vademecum.storage import flags, piles
from vademecum.storage.common import NotFoundError


def test_a_pile_round_trips(connection: sqlite3.Connection) -> None:
    created = piles.create_pile(
        connection, title="Antimicrobial stewardship", tier="high", description="ID"
    )
    assert created.id.startswith("pil_")
    assert created.tier == "high"
    assert created.item_count == 0
    assert piles.get_pile(connection, created.id) == created


def test_the_three_tiers_are_the_supported_tiers() -> None:
    assert piles.TIERS == ("low", "mid", "high")


def test_piles_can_be_listed_by_tier(connection: sqlite3.Connection) -> None:
    piles.create_pile(connection, title="A", tier="low")
    piles.create_pile(connection, title="B", tier="high")
    assert [pile.title for pile in piles.list_piles(connection, tier="high")] == ["B"]
    assert len(piles.list_piles(connection)) == 2


def test_an_item_belongs_to_a_pile_and_carries_a_content_hash(
    connection: sqlite3.Connection,
) -> None:
    pile = piles.create_pile(connection, title="Sepsis", tier="mid")
    item = piles.create_item(
        connection, pile_id=pile.id, title="Lactate clearance", body="Notes", source="Lecture"
    )
    assert item.pile_id == pile.id
    assert len(item.content_hash) == 64
    assert piles.get_pile(connection, pile.id).item_count == 1


def test_editing_an_item_updates_its_content_hash(connection: sqlite3.Connection) -> None:
    pile = piles.create_pile(connection, title="Sepsis", tier="mid")
    item = piles.create_item(connection, pile_id=pile.id, title="One")
    edited = piles.update_item(connection, item.id, title="Two")
    assert edited.content_hash != item.content_hash
    assert edited.created_at == item.created_at


def test_an_item_cannot_be_created_in_a_pile_that_does_not_exist(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(NotFoundError):
        piles.create_item(connection, pile_id="pil_missing", title="Orphan")


def test_deleting_a_pile_deletes_its_items(connection: sqlite3.Connection) -> None:
    pile = piles.create_pile(connection, title="Sepsis", tier="mid")
    item = piles.create_item(connection, pile_id=pile.id, title="Lactate")
    piles.delete_pile(connection, pile.id)
    with pytest.raises(NotFoundError):
        piles.get_item(connection, item.id)


def test_deleting_a_pile_keeps_the_flags_that_pointed_at_it(
    connection: sqlite3.Connection,
) -> None:
    """A flag is the owner's own words; a pile going away must not take it."""
    pile = piles.create_pile(connection, title="Sepsis", tier="mid")
    flag = flags.create_flag(connection, text="Unsure about lactate targets", pile_id=pile.id)
    piles.delete_pile(connection, pile.id)
    kept = flags.get_flag(connection, flag.id)
    assert kept.text == "Unsure about lactate targets"
    assert kept.pile_id is None


def test_a_flag_needs_only_text(connection: sqlite3.Connection) -> None:
    flag = flags.create_flag(connection, text="Had to look up the HIT 4T score")
    assert flag.id.startswith("kgf_")
    assert flag.topic is None
    assert flag.pile_id is None
    assert flag.status == "open"
    assert flag.addressed_at is None


def test_addressing_a_flag_stamps_it_and_reopening_clears_the_stamp(
    connection: sqlite3.Connection,
) -> None:
    flag = flags.create_flag(connection, text="Renal dosing of vancomycin")
    addressed = flags.update_flag(connection, flag.id, status="addressed")
    assert addressed.addressed_at is not None
    reopened = flags.update_flag(connection, flag.id, status="open")
    assert reopened.addressed_at is None


def test_deletion_is_real(connection: sqlite3.Connection) -> None:
    flag = flags.create_flag(connection, text="Something")
    flags.delete_flag(connection, flag.id)
    with pytest.raises(NotFoundError):
        flags.get_flag(connection, flag.id)
    remaining = connection.execute(
        "SELECT COUNT(*) FROM knowledge_gap_flags WHERE id = ?", (flag.id,)
    ).fetchone()[0]
    assert remaining == 0


def test_records_survive_closing_and_reopening_the_database(data_dir: Path) -> None:
    path = data_dir / "vademecum.sqlite3"
    first = connect(path)
    apply_migrations(first)
    pile = piles.create_pile(first, title="Endocarditis", tier="high")
    piles.create_item(first, pile_id=pile.id, title="Duke criteria")
    flags.create_flag(first, text="Unsure when to image")
    first.close()

    second = connect(path)
    assert apply_migrations(second) == []
    assert [p.title for p in piles.list_piles(second)] == ["Endocarditis"]
    assert piles.get_pile(second, pile.id).item_count == 1
    assert len(flags.list_flags(second)) == 1
    second.close()
