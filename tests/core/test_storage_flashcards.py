"""
Tests for FlashcardStore in storage.py — flashcard CRUD, FSRS grading, review stats.

Covers:
- add_card creates a card with expected fields
- get_cards_for_concept returns correct cards
- grade_card_fsrs updates scheduling fields (mocked FSRSEngine)
- get_review_stats returns dict with expected keys
- Uses temp SQLite DB for each test via pytest tmp_path
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from services.common.storage import StorageManager


class TestFlashcardStoreAddCard(unittest.TestCase):
    """Tests for FlashcardStore.add_card()."""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp(prefix="helga_test_fc_")
        self.storage = StorageManager(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_add_card_returns_uid(self):
        uid = self.storage.flashcards.add_card("course1", "concept1", "Front", "Back")
        self.assertIsNotNone(uid)
        self.assertTrue(uid.startswith("card_"))

    def test_add_card_stores_front_back(self):
        uid = self.storage.flashcards.add_card("course1", "concept1", "What is X?", "X is Y")
        cards = self.storage.flashcards.get_cards_for_concept("concept1")
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["front"], "What is X?")
        self.assertEqual(cards[0]["back"], "X is Y")

    def test_add_card_stores_course_uid(self):
        self.storage.flashcards.add_card("course_abc", "con1", "F", "B")
        cards = self.storage.flashcards.get_cards_for_course("course_abc")
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["course_uid"], "course_abc")

    def test_add_card_default_status_is_new(self):
        self.storage.flashcards.add_card("c1", "con1", "F", "B")
        cards = self.storage.flashcards.get_cards_for_concept("con1")
        self.assertEqual(cards[0]["status"], "new")

    def test_add_multiple_cards(self):
        self.storage.flashcards.add_card("c1", "con1", "F1", "B1")
        self.storage.flashcards.add_card("c1", "con1", "F2", "B2")
        self.storage.flashcards.add_card("c1", "con1", "F3", "B3")
        cards = self.storage.flashcards.get_cards_for_concept("con1")
        self.assertEqual(len(cards), 3)


class TestFlashcardStoreGetCards(unittest.TestCase):
    """Tests for get_cards_for_concept and get_cards_for_course."""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp(prefix="helga_test_fc_")
        self.storage = StorageManager(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_get_cards_for_concept_empty(self):
        cards = self.storage.flashcards.get_cards_for_concept("nonexistent")
        self.assertEqual(cards, [])

    def test_get_cards_for_concept_filters_correctly(self):
        self.storage.flashcards.add_card("c1", "con1", "F1", "B1")
        self.storage.flashcards.add_card("c1", "con2", "F2", "B2")
        self.storage.flashcards.add_card("c1", "con1", "F3", "B3")
        cards = self.storage.flashcards.get_cards_for_concept("con1")
        self.assertEqual(len(cards), 2)
        for card in cards:
            self.assertEqual(card["concept_uid"], "con1")

    def test_get_cards_for_course_filters_correctly(self):
        self.storage.flashcards.add_card("course_a", "con1", "F1", "B1")
        self.storage.flashcards.add_card("course_a", "con2", "F2", "B2")
        self.storage.flashcards.add_card("course_b", "con3", "F3", "B3")
        cards_a = self.storage.flashcards.get_cards_for_course("course_a")
        self.assertEqual(len(cards_a), 2)
        cards_b = self.storage.flashcards.get_cards_for_course("course_b")
        self.assertEqual(len(cards_b), 1)

    def test_get_cards_returns_dicts(self):
        self.storage.flashcards.add_card("c1", "con1", "F", "B")
        cards = self.storage.flashcards.get_cards_for_concept("con1")
        self.assertIsInstance(cards[0], dict)
        self.assertIn("uid", cards[0])
        self.assertIn("front", cards[0])
        self.assertIn("back", cards[0])
        self.assertIn("status", cards[0])


class TestFlashcardStoreGradeCardFsrs(unittest.TestCase):
    """Tests for grade_card_fsrs with mocked FSRSEngine."""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp(prefix="helga_test_fc_")
        self.storage = StorageManager(self.tmp_dir)

        # Create a mock FSRSEngine
        self.mock_fsrs = MagicMock()
        self.mock_fsrs.calculate_memory.return_value = (5.0, 4.5)
        self.mock_fsrs.next_interval.return_value = 3
        self.mock_fsrs.get_retention.return_value = 0.92

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_grade_card_returns_dict(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        result = self.storage.flashcards.grade_card_fsrs(uid, 3, self.mock_fsrs)
        self.assertIsInstance(result, dict)

    def test_grade_card_has_expected_keys(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        result = self.storage.flashcards.grade_card_fsrs(uid, 3, self.mock_fsrs)
        expected_keys = ["uid", "rating", "interval_days", "next_review_date",
                         "stability", "difficulty", "repetitions", "lapses", "retention"]
        for key in expected_keys:
            self.assertIn(key, result, f"Missing key: {key}")

    def test_grade_card_updates_stability(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        result = self.storage.flashcards.grade_card_fsrs(uid, 3, self.mock_fsrs)
        self.assertEqual(result["stability"], 5.0)

    def test_grade_card_updates_difficulty(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        result = self.storage.flashcards.grade_card_fsrs(uid, 3, self.mock_fsrs)
        self.assertEqual(result["difficulty"], 4.5)

    def test_grade_card_updates_interval(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        result = self.storage.flashcards.grade_card_fsrs(uid, 3, self.mock_fsrs)
        self.assertEqual(result["interval_days"], 3)

    def test_grade_card_sets_next_review_date(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        result = self.storage.flashcards.grade_card_fsrs(uid, 3, self.mock_fsrs)
        expected_date = (date.today() + timedelta(days=3)).isoformat()
        self.assertEqual(result["next_review_date"], expected_date)

    def test_grade_card_increments_repetitions(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        result1 = self.storage.flashcards.grade_card_fsrs(uid, 3, self.mock_fsrs)
        self.assertEqual(result1["repetitions"], 1)
        result2 = self.storage.flashcards.grade_card_fsrs(uid, 3, self.mock_fsrs)
        self.assertEqual(result2["repetitions"], 2)

    def test_grade_card_again_increments_lapses(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        result = self.storage.flashcards.grade_card_fsrs(uid, 1, self.mock_fsrs)
        self.assertEqual(result["lapses"], 1)

    def test_grade_card_again_sets_interval_to_1(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        result = self.storage.flashcards.grade_card_fsrs(uid, 1, self.mock_fsrs)
        self.assertEqual(result["interval_days"], 1)

    def test_grade_card_good_no_lapses(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        result = self.storage.flashcards.grade_card_fsrs(uid, 3, self.mock_fsrs)
        self.assertEqual(result["lapses"], 0)

    def test_grade_card_updates_db(self):
        """Verify the DB is actually updated after grading."""
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        self.storage.flashcards.grade_card_fsrs(uid, 3, self.mock_fsrs)
        cards = self.storage.flashcards.get_cards_for_concept("con1")
        self.assertEqual(cards[0]["status"], "review")
        self.assertIsNotNone(cards[0]["stability"])
        self.assertIsNotNone(cards[0]["last_review_date"])

    def test_grade_card_nonexistent_raises(self):
        with self.assertRaises(ValueError):
            self.storage.flashcards.grade_card_fsrs("card_nonexistent", 3, self.mock_fsrs)

    def test_grade_card_clamps_rating(self):
        """Ratings outside 1-4 should be clamped."""
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        result = self.storage.flashcards.grade_card_fsrs(uid, 5, self.mock_fsrs)
        self.assertEqual(result["rating"], 4)

    def test_grade_card_calls_fsrs_engine(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        self.storage.flashcards.grade_card_fsrs(uid, 3, self.mock_fsrs)
        self.mock_fsrs.calculate_memory.assert_called_once()
        self.mock_fsrs.next_interval.assert_called_once()


class TestFlashcardStoreGetReviewStats(unittest.TestCase):
    """Tests for get_review_stats."""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp(prefix="helga_test_fc_")
        self.storage = StorageManager(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_returns_dict(self):
        stats = self.storage.flashcards.get_review_stats()
        self.assertIsInstance(stats, dict)

    def test_has_expected_keys(self):
        stats = self.storage.flashcards.get_review_stats()
        expected_keys = ["due_today", "upcoming_7d", "avg_retention", "calendar", "total_cards"]
        for key in expected_keys:
            self.assertIn(key, stats, f"Missing key: {key}")

    def test_empty_db_returns_zeros(self):
        stats = self.storage.flashcards.get_review_stats()
        self.assertEqual(stats["due_today"], 0)
        self.assertEqual(stats["upcoming_7d"], 0)
        self.assertEqual(stats["avg_retention"], 0)

    def test_due_cards_counted(self):
        # Card with no review date = due
        self.storage.flashcards.add_card("c1", "con1", "F1", "B1")
        stats = self.storage.flashcards.get_review_stats()
        self.assertGreaterEqual(stats["due_today"], 1)

    def test_future_cards_not_due_today(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F1", "B1")
        future = (date.today() + timedelta(days=5)).isoformat()
        self.storage.flashcards.update_card(uid, next_review_date=future)
        stats = self.storage.flashcards.get_review_stats()
        self.assertEqual(stats["due_today"], 0)

    def test_upcoming_7d_count(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F1", "B1")
        future = (date.today() + timedelta(days=3)).isoformat()
        self.storage.flashcards.update_card(uid, next_review_date=future)
        stats = self.storage.flashcards.get_review_stats()
        self.assertGreaterEqual(stats["upcoming_7d"], 1)

    def test_filter_by_course_uid(self):
        self.storage.flashcards.add_card("course_a", "con1", "F1", "B1")
        self.storage.flashcards.add_card("course_b", "con2", "F2", "B2")
        stats_a = self.storage.flashcards.get_review_stats(course_uid="course_a")
        self.assertGreaterEqual(stats_a["due_today"], 1)

    def test_calendar_is_dict(self):
        stats = self.storage.flashcards.get_review_stats()
        self.assertIsInstance(stats["calendar"], dict)


class TestFlashcardStoreDueCards(unittest.TestCase):
    """Tests for get_due_cards."""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp(prefix="helga_test_fc_")
        self.storage = StorageManager(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_new_card_is_due(self):
        self.storage.flashcards.add_card("c1", "con1", "F", "B")
        due = self.storage.flashcards.get_due_cards("c1")
        self.assertEqual(len(due), 1)

    def test_past_card_is_due(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        past = (date.today() - timedelta(days=1)).isoformat()
        self.storage.flashcards.update_card(uid, next_review_date=past)
        due = self.storage.flashcards.get_due_cards("c1")
        self.assertEqual(len(due), 1)

    def test_future_card_not_due(self):
        uid = self.storage.flashcards.add_card("c1", "con1", "F", "B")
        future = (date.today() + timedelta(days=5)).isoformat()
        self.storage.flashcards.update_card(uid, next_review_date=future)
        due = self.storage.flashcards.get_due_cards("c1")
        self.assertEqual(len(due), 0)


if __name__ == '__main__':
    unittest.main()
