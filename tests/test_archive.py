import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from src import archive, paths, transcript_search


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / ".speakrflow"
        patcher = mock.patch.object(paths, "ARCHIVE_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_recordings_and_transcripts_are_numbered_in_order(self):
        first = archive.save_recording(b"one")
        archive.save_transcript("hello there", first)
        second = archive.save_recording(b"two")
        archive.save_transcript("general kenobi", second)

        self.assertEqual((first, second), (1, 2))
        self.assertEqual((self.dir / "1.wav").read_bytes(), b"one")
        self.assertEqual((self.dir / "2.txt").read_text(), "general kenobi\n")

    def test_numbering_continues_after_existing_files_and_gaps(self):
        self.dir.mkdir()
        (self.dir / "7.txt").write_text("old")
        (self.dir / "notes.txt").write_text("ignored")

        self.assertEqual(archive.save_recording(b"x"), 8)

    def test_failed_transcription_keeps_audio_and_next_number_moves_on(self):
        archive.save_recording(b"x")          # transcription failed: no 1.txt
        self.assertEqual(archive.save_transcript("no audio"), 2)
        self.assertEqual(archive.save_recording(b"y"), 3)

    def test_list_recordings_newest_first_with_missing_transcript(self):
        archive.save_transcript("first", archive.save_recording(b"a"))
        archive.save_recording(b"b")

        entries = archive.list_recordings()

        self.assertEqual([e["number"] for e in entries], [2, 1])
        self.assertIsNone(entries[0]["text"])
        self.assertEqual(entries[1]["text"], "first")
        self.assertIsNotNone(entries[1]["time"])

    def test_list_recordings_when_folder_missing(self):
        self.assertEqual(archive.list_recordings(), [])

    def test_search_finds_every_instance_case_insensitively(self):
        archive.save_transcript("Ship the build. Then ship it again.")
        archive.save_transcript("nothing relevant")
        archive.save_transcript("SHIP")

        results = archive.search("ship")

        self.assertEqual([r["number"] for r in results], [3, 1])
        self.assertEqual(results[1]["matches"], [(0, 4), (21, 25)])

    def test_search_phrase_spans_line_breaks(self):
        archive.save_transcript("see you\ntomorrow")

        results = archive.search("  you   tomorrow ")

        self.assertEqual(results[0]["matches"], [(4, 16)])

    def test_search_treats_query_literally(self):
        archive.save_transcript("costs $5 (maybe)")

        self.assertEqual(len(archive.search("$5 (maybe")), 1)
        self.assertEqual(archive.search(".*"), [])

    def test_blank_search_lists_everything(self):
        archive.save_recording(b"a")
        archive.save_transcript("b")

        results = archive.search("")

        self.assertEqual([(r["number"], r["matches"]) for r in results], [(2, []), (1, [])])

    def test_search_sees_rewritten_transcript(self):
        number = archive.save_transcript("alpha")
        self.assertEqual(len(archive.search("alpha")), 1)
        archive.save_transcript("beta, a longer transcript", number)

        self.assertEqual(archive.search("alpha"), [])
        self.assertEqual(len(archive.search("beta")), 1)

    def test_snippet_trims_to_words_and_marks_match(self):
        text = "one two three four five six seven eight nine ten"
        start = text.index("five")

        excerpt, s, e = archive.snippet(text, start, start + 4, before=10, after=10)

        self.assertEqual(excerpt[s:e], "five")
        self.assertTrue(excerpt.startswith("…"))
        self.assertTrue(excerpt.endswith("…"))
        self.assertNotIn("\n", excerpt)
        self.assertEqual(excerpt, "…four five six…")

    def test_snippet_without_trimming_has_no_ellipses(self):
        excerpt, s, e = archive.snippet("hi\nthere", 3, 8)

        self.assertEqual(excerpt, "hi there")
        self.assertEqual((s, e), (3, 8))


class SearchWindowHelperTests(unittest.TestCase):
    def test_rows_group_mentions_under_their_recording(self):
        results = [{"number": 3, "matches": [(0, 1), (5, 6)]}, {"number": 1, "matches": [(2, 3)]}]

        rows = transcript_search.build_rows(results)

        self.assertEqual([(kind, r["number"], i) for kind, r, i in rows], [
            ("recording", 3, None), ("match", 3, 0), ("match", 3, 1),
            ("recording", 1, None), ("match", 1, 0),
        ])

    def test_summary_text(self):
        results = [{"matches": [(0, 1), (2, 3)]}, {"matches": [(0, 1)]}]

        self.assertEqual(transcript_search.summary("hi", results, 9), "3 mentions in 2 recordings")
        self.assertEqual(transcript_search.summary(" hi  there ", [], 9), "No matches for “hi there”")
        self.assertTrue(transcript_search.summary("", [], 1).startswith("1 recording."))
        self.assertIn("No recordings yet", transcript_search.summary("", [], 0))

    def test_when_uses_relative_days(self):
        now = datetime(2026, 10, 29, 18, 0)

        self.assertEqual(transcript_search.when(datetime(2026, 10, 29, 9, 5), now), "Today at 9:05 AM")
        self.assertEqual(transcript_search.when(datetime(2026, 10, 28, 15, 4), now), "Yesterday at 3:04 PM")
        self.assertEqual(transcript_search.when(datetime(2026, 3, 3, 0, 1), now), "Mar 3, 2026 at 12:01 AM")


if __name__ == "__main__":
    unittest.main()
