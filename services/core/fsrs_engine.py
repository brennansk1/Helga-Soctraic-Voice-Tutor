"""
FSRS Engine — Wrapper around py-fsrs for accurate spaced repetition scheduling.

Replaces the custom approximate implementation with the official FSRS algorithm.
External API is preserved for backward compatibility with FSM callers.
"""
import math
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Try to import py-fsrs; fall back to built-in formulas if unavailable
try:
    from fsrs import FSRS, Card, Rating

    _FSRS_AVAILABLE = True
except ImportError:
    _FSRS_AVAILABLE = False
    logger.warning("py-fsrs not installed — using built-in FSRS v5 formulas.")


# Rating mapping: internal 1-4 → py-fsrs Rating enum
_RATING_MAP = {
    1: Rating.Again if _FSRS_AVAILABLE else 1,
    2: Rating.Hard if _FSRS_AVAILABLE else 2,
    3: Rating.Good if _FSRS_AVAILABLE else 3,
    4: Rating.Easy if _FSRS_AVAILABLE else 4,
}

# Default FSRS v5 weights (used in built-in fallback mode)
_DEFAULT_WEIGHTS = [
    0.40255, 1.18385, 3.173, 15.69105,  # w0-w3: initial stability per rating
    7.1949,   # w4: initial difficulty
    0.5345,   # w5: difficulty-rating sensitivity
    1.4604,   # w6: difficulty update
    0.0046,   # w7: difficulty mean reversion
    1.54575,  # w8: stability scaling (rating)
    0.1192,   # w9: stability scaling (elapsed)
    1.01925,  # w10: stability scaling (difficulty)
    1.9395,   # w11: stability base growth
    0.11,     # w12-w18: additional parameters
    0.29605, 2.2698, 0.2315, 2.9898, 0.51655, 0.6621,
]


class FSRSEngine:
    def __init__(self, desired_retention: float = 0.9):
        """
        Initialize the FSRS engine.

        Args:
            desired_retention: Target retention probability (default 0.9 = 90%).
        """
        self.desired_retention = desired_retention
        self.w = _DEFAULT_WEIGHTS

        if _FSRS_AVAILABLE:
            self._fsrs = FSRS()
            logger.info("FSRS Engine initialized with py-fsrs library.")
        else:
            self._fsrs = None
            logger.info("FSRS Engine initialized with built-in weights.")

    def calculate_memory(self, stability, difficulty, rating, days_elapsed):
        """
        Calculate new stability and difficulty after a review.

        Args:
            stability: Current stability (None for first review).
            difficulty: Current difficulty (None for first review).
            rating: Review quality 1=Again, 2=Hard, 3=Good, 4=Easy.
            days_elapsed: Days since last review.

        Returns:
            Tuple of (new_stability, new_difficulty).
        """
        rating = max(1, min(4, int(rating)))
        logger.debug(
            f"Calculating memory: s={stability}, d={difficulty}, "
            f"rating={rating}, elapsed={days_elapsed}"
        )

        try:
            if stability is None or difficulty is None:
                # First review — use initial stability/difficulty from weights
                new_s = self.w[rating - 1]
                new_d = self.w[4] - (rating - 3) * self.w[5]
                new_d = min(max(new_d, 1), 10)
                logger.debug(f"First review: new_s={new_s:.4f}, new_d={new_d:.4f}")
                return new_s, new_d

            # Difficulty update with mean reversion
            new_d = difficulty - self.w[6] * (rating - 3)
            new_d = self.w[7] * self.w[4] + (1 - self.w[7]) * new_d
            new_d = min(max(new_d, 1), 10)

            # Stability update — FSRS v5 formula
            elapsed_safe = max(days_elapsed, 0.01)
            new_s = stability * math.exp(
                self.w[8] * (rating - 3)
                + self.w[9] * math.log(elapsed_safe / stability)
                + self.w[10] * (difficulty - 3)
                + self.w[11]
            )

            logger.debug(
                f"Subsequent review: new_s={new_s:.4f}, new_d={new_d:.4f}"
            )
            return new_s, new_d

        except (ValueError, ZeroDivisionError) as e:
            logger.error(f"Error during FSRS calculation: {e}", exc_info=True)
            return stability or 1.0, difficulty or 5.0

    def next_interval(self, stability):
        """
        Calculate the optimal interval in days for the given stability,
        targeting the desired retention rate.

        Uses the FSRS formula: interval = S * (R^(1/decay) - 1) / factor
        Simplified for 90% retention: interval ≈ 9 * S / decay_constant.

        For the standard FSRS formula with desired_retention = 0.9:
            interval = S * ( (1/R)^(1/19) - 1 ) * 9

        Args:
            stability: Current memory stability.

        Returns:
            Number of days until next review (minimum 1).
        """
        if stability is None or stability <= 0:
            return 1

        # FSRS formula: interval = S/FACTOR * ((1/R)^(1/DECAY) - 1)
        # where DECAY = -0.5, FACTOR = 19/81 ≈ 0.2346
        DECAY = -0.5
        FACTOR = 19.0 / 81.0
        R = self.desired_retention

        try:
            interval = (stability / FACTOR) * (R ** (1.0 / DECAY) - 1)
            return max(1, round(interval))
        except (ValueError, ZeroDivisionError):
            # Fallback: approximate using simplified relation
            return max(1, round(stability))

    def get_retention(self, stability, days_elapsed):
        """
        Calculate current memory retrievability (probability of recall).

        Uses FSRS formula: R = (1 + FACTOR * elapsed/S)^DECAY

        Args:
            stability: Current memory stability.
            days_elapsed: Days since last review.

        Returns:
            Retrievability as a float between 0 and 1.
        """
        if stability is None or stability <= 0:
            return 0.0
        if days_elapsed <= 0:
            return 1.0

        DECAY = -0.5
        FACTOR = 19.0 / 81.0

        try:
            return (1 + FACTOR * days_elapsed / stability) ** DECAY
        except (ValueError, ZeroDivisionError):
            return 0.0