"""Resumable coverage, idempotent upload, and safe concurrent writes."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from vademecum.ingest.extract import Extraction, Segment
from vademecum.storage import learning, sources
from vademecum.storage import jobs
from vademecum.storage import piles as pile_store


def extraction(*texts: str) -> Extraction:
    return Extraction(
        "extracted",
        "",
        "section",
        len(texts),
        tuple(
            Segment(index, "section", f"section {index + 1}", text)
            for index, text in enumerate(texts)
        ),
    )


def upload(connection, pile_id, text, *, digest="a" * 64, name="a.txt"):
    return sources.store_upload(
        connection,
        pile_id=pile_id,
        display_name=name,
        media_type="text/plain",
        sha256=digest,
        byte_size=len(text),
        confidence="mid",
        extraction=extraction(text) if isinstance(text, str) else extraction(*text),
    )


@pytest.fixture()
def pile(connection):
    return pile_store.create_pile(connection, title="Pile", tier="mid")


def drain(connection, pile_id, *, rounds: int = 50) -> list[sources.Excerpt]:
    """Run batches to exhaustion, committing each. Returns everything sent."""
    sent: list[sources.Excerpt] = []
    for _ in range(rounds):
        batch = sources.next_batch(connection, pile_id)
        if not batch.excerpts:
            return sent
        batch_id = sources.record_batch(connection, pile_id, batch)
        sent.extend(
            sources.claim_batch(
                connection,
                pile_id=pile_id,
                batch_id=batch_id,
                expected_hash=batch.selection_hash,
            )
        )
        sources.commit_batch(connection, batch_id)
    raise AssertionError("coverage did not terminate")


class TestCoverage:
    def test_pile_list_uses_the_same_character_cursor_as_build_preview(
        self, connection, pile
    ) -> None:
        body = " ".join(f"word{index:05d}" for index in range(12000))
        source = upload(connection, pile.id, body).source
        batch = sources.next_batch(connection, pile.id)
        batch_id = sources.record_batch(connection, pile.id, batch)
        sources.claim_batch(
            connection, pile_id=pile.id, batch_id=batch_id,
            expected_hash=batch.selection_hash,
        )
        sources.commit_batch(connection, batch_id)
        expected = sources.pile_coverage(connection, pile.id).as_dict()
        assert 0 < expected["chars_covered"] < expected["chars_total"]
        assert expected["complete"] is False
        actual = pile_store.list_piles(connection)[0].as_dict()["coverage"]
        assert {key: actual[key] for key in expected} == expected
        sources.set_excluded(connection, source.id, excluded=True)
        excluded = pile_store.get_pile(connection, pile.id).as_dict()["coverage"]
        assert excluded["chars_total"] == excluded["chars_covered"] == 0
        assert excluded["complete"] is False

    def test_every_character_of_a_long_segment_is_eventually_sent(
        self, connection, pile
    ) -> None:
        """A 12,000-character page is not 'done' after 2,400 of it went out."""
        body = " ".join(f"word{index:05d}" for index in range(1600))
        upload(connection, pile.id, body)

        sent = drain(connection, pile.id)
        assert len(sent) > 1, "one long page must span several excerpts"
        rebuilt = " ".join(excerpt.text for excerpt in sent)
        assert rebuilt.split() == body.split()
        assert sources.pile_coverage(connection, pile.id).complete

    def test_the_last_short_tail_is_not_left_behind(self, connection, pile) -> None:
        body = ("x" * 2400) + " " + ("y" * 2400) + " tail-marker-at-the-very-end"
        upload(connection, pile.id, body)

        sent = drain(connection, pile.id)
        assert "tail-marker-at-the-very-end" in " ".join(e.text for e in sent)
        assert sources.pile_coverage(connection, pile.id).complete

    def test_a_second_batch_does_not_resend_the_first(self, connection, pile) -> None:
        body = " ".join(f"token{index:05d}" for index in range(9000))
        upload(connection, pile.id, body)

        first = sources.next_batch(connection, pile.id)
        first_id = sources.record_batch(connection, pile.id, first)
        sources.claim_batch(
            connection,
            pile_id=pile.id,
            batch_id=first_id,
            expected_hash=first.selection_hash,
        )
        sources.commit_batch(connection, first_id)

        second = sources.next_batch(connection, pile.id)
        assert second.excerpts, "there is more material, so there is another batch"
        assert second.excerpts[0].start >= first.excerpts[-1].end
        overlap = {e.text for e in first.excerpts} & {e.text for e in second.excerpts}
        assert overlap == set()

    def test_a_failed_batch_advances_nothing(self, connection, pile) -> None:
        upload(connection, pile.id, "A passage that will be offered twice over.")
        batch = sources.next_batch(connection, pile.id)
        batch_id = sources.record_batch(connection, pile.id, batch)
        sources.claim_batch(
            connection,
            pile_id=pile.id,
            batch_id=batch_id,
            expected_hash=batch.selection_hash,
        )
        sources.close_batch(connection, batch_id, status="failed")

        assert sources.pile_coverage(connection, pile.id).chars_covered == 0
        again = sources.next_batch(connection, pile.id)
        assert [e.text for e in again.excerpts] == [e.text for e in batch.excerpts]

    def test_a_cancelled_batch_advances_nothing(self, connection, pile) -> None:
        upload(connection, pile.id, "Another passage, cancelled part way through.")
        batch = sources.next_batch(connection, pile.id)
        batch_id = sources.record_batch(connection, pile.id, batch)
        sources.close_batch(connection, batch_id, status="cancelled")
        assert sources.pile_coverage(connection, pile.id).chars_covered == 0

    def test_percent_never_reads_a_hundred_while_incomplete(
        self, connection, pile
    ) -> None:
        upload(connection, pile.id, "x" * 5000)
        batch = sources.next_batch(connection, pile.id)
        batch_id = sources.record_batch(connection, pile.id, batch)
        sources.claim_batch(
            connection,
            pile_id=pile.id,
            batch_id=batch_id,
            expected_hash=batch.selection_hash,
        )
        sources.commit_batch(connection, batch_id)
        upload(connection, pile.id, "y" * 40000, digest="b" * 64, name="b.txt")

        coverage = sources.pile_coverage(connection, pile.id)
        assert not coverage.complete
        assert coverage.percent < 100

    def test_a_selection_hash_covers_the_exact_characters(
        self, connection, pile
    ) -> None:
        upload(connection, pile.id, "A stable passage for hashing purposes here.")
        first = sources.next_batch(connection, pile.id)
        second = sources.next_batch(connection, pile.id)
        assert first.selection_hash == second.selection_hash

        # Re-extracting with different text must change the hash even though the
        # segment count and ordering are the same.
        upload(connection, pile.id, "A DIFFERENT passage for hashing purposes here.")
        third = sources.next_batch(connection, pile.id)
        assert third.selection_hash != first.selection_hash


class TestUploadIdempotence:
    def test_identical_bytes_and_extraction_preserve_segment_ids(
        self, connection, pile
    ) -> None:
        first = upload(connection, pile.id, "One passage that stays exactly the same.")
        ids = [segment.id for segment in sources.list_segments(connection, first.source.id)]

        second = upload(connection, pile.id, "One passage that stays exactly the same.")
        assert second.outcome == "duplicate"
        assert [
            segment.id for segment in sources.list_segments(connection, second.source.id)
        ] == ids

    def test_an_unchanged_anchor_survives_a_later_segment_being_added(
        self, connection, pile
    ) -> None:
        """The defect this exists for: DELETE + FK SET NULL orphaned anchors."""
        head = "The first passage, which does not change between extractions."
        tail = "A second passage, also unchanged across the two reads."
        first = upload(connection, pile.id, [head, tail])
        segments = sources.list_segments(connection, first.source.id)
        run = jobs.start_run(
            connection, pile_id=pile.id, source_count=1, excerpt_count=1, excerpt_chars=1
        )
        point_id, _ = learning.upsert_point(
            connection,
            pile_id=pile.id,
            generation_id=run.id,
            draft=learning.DraftPoint(
                claim="A claim about the first passage",
                detail="",
                topics=(),
                citations=(
                    learning.DraftCitation(
                        first.source.id, segments[0].id, "section 1", head[:40]
                    ),
                ),
            ),
        )
        anchored = connection.execute(
            "SELECT segment_id FROM learning_point_sources WHERE learning_point_id = ?",
            (point_id,),
        ).fetchone()["segment_id"]
        assert anchored == segments[0].id

        # A third passage appears; the first two are byte-identical.
        upload(connection, pile.id, [head, tail, "A newly appended third passage."])

        after = connection.execute(
            "SELECT segment_id FROM learning_point_sources WHERE learning_point_id = ?",
            (point_id,),
        ).fetchone()["segment_id"]
        assert after is not None, "the anchor must not have been nulled"
        assert after == anchored

    def test_reordering_keeps_the_anchor_on_its_own_text(
        self, connection, pile
    ) -> None:
        alpha = "Alpha passage, distinctive enough to be recognised again."
        beta = "Beta passage, equally distinctive and quite separate."
        first = upload(connection, pile.id, [alpha, beta])
        segments = sources.list_segments(connection, first.source.id)
        run = jobs.start_run(
            connection, pile_id=pile.id, source_count=1, excerpt_count=1, excerpt_chars=1
        )
        point_id, _ = learning.upsert_point(
            connection,
            pile_id=pile.id,
            generation_id=run.id,
            draft=learning.DraftPoint(
                claim="A claim about alpha",
                detail="",
                topics=(),
                citations=(
                    learning.DraftCitation(
                        first.source.id, segments[0].id, "section 1", alpha[:40]
                    ),
                ),
            ),
        )
        original = segments[0].id

        upload(connection, pile.id, [beta, alpha])  # swapped

        anchored = connection.execute(
            "SELECT segment_id FROM learning_point_sources WHERE learning_point_id = ?",
            (point_id,),
        ).fetchone()["segment_id"]
        assert anchored == original
        assert sources.get_segment(connection, anchored).text == alpha

    def test_changed_text_holds_what_depended_on_it(self, connection, pile) -> None:
        first = upload(connection, pile.id, "The original wording of this passage.")
        segments = sources.list_segments(connection, first.source.id)
        run = jobs.start_run(
            connection, pile_id=pile.id, source_count=1, excerpt_count=1, excerpt_chars=1
        )
        point_id, _ = learning.upsert_point(
            connection,
            pile_id=pile.id,
            generation_id=run.id,
            draft=learning.DraftPoint(
                claim="A claim",
                detail="",
                topics=(),
                citations=(
                    learning.DraftCitation(
                        first.source.id, segments[0].id, "section 1", "The original wording"
                    ),
                ),
            ),
        )
        second = upload(connection, pile.id, "Completely rewritten wording of this passage.")
        assert second.outcome == "re_extracted"

        point = learning.get_point(connection, point_id)
        assert point.held
        assert point.review_state == "needs_re_review"


class TestOriginalFiles:
    def test_concurrent_writes_of_the_same_bytes_do_not_collide(
        self, tmp_path: Path
    ) -> None:
        directory = tmp_path / "sources"
        payload = b"x" * (512 * 1024)
        digest = sources.digest(payload)
        name = sources.stored_name_for(digest, "pdf")
        errors: list[BaseException] = []

        def write() -> None:
            try:
                sources.write_original(directory, name, payload, digest)
            except BaseException as exc:  # pragma: no cover - the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=write) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        target = directory / name
        assert target.read_bytes() == payload
        assert sources.file_digest(target) == digest
        # No partial files left behind by any of the eight.
        assert [p.name for p in directory.iterdir() if p.name != name] == []

    def test_a_corrupt_existing_file_of_the_right_length_is_rewritten(
        self, tmp_path: Path
    ) -> None:
        """A size check would have trusted this. A digest check does not."""
        directory = tmp_path / "sources"
        directory.mkdir()
        payload = b"correct-content-here"
        digest = sources.digest(payload)
        name = sources.stored_name_for(digest, "text")
        (directory / name).write_bytes(b"WRONG-content-here!!")  # same length

        sources.write_original(directory, name, payload, digest)
        assert (directory / name).read_bytes() == payload


class TestExclusion:
    def test_excluding_a_source_holds_what_it_produced(self, connection, pile) -> None:
        first = upload(connection, pile.id, "A passage that will later be excluded.")
        segments = sources.list_segments(connection, first.source.id)
        run = jobs.start_run(
            connection, pile_id=pile.id, source_count=1, excerpt_count=1, excerpt_chars=1
        )
        point_id, _ = learning.upsert_point(
            connection,
            pile_id=pile.id,
            generation_id=run.id,
            draft=learning.DraftPoint(
                claim="A claim from an excluded source",
                detail="",
                topics=(),
                citations=(
                    learning.DraftCitation(
                        first.source.id, segments[0].id, "section 1", "A passage that will later"
                    ),
                ),
            ),
        )
        sources.set_excluded(connection, first.source.id, excluded=True)

        point = learning.get_point(connection, point_id)
        assert point.held
        assert "excluded" in point.hold_reason.lower()

    def test_an_excluded_source_leaves_the_batch(self, connection, pile) -> None:
        first = upload(connection, pile.id, "Text that will stop being offered.")
        assert sources.next_batch(connection, pile.id).excerpts
        sources.set_excluded(connection, first.source.id, excluded=True)
        assert sources.next_batch(connection, pile.id).excerpts == ()
