"""
FSRS Engine — spaced-repetition scheduling using the FSRS-5 algorithm.

Implements the published FSRS-5 difficulty/stability/retrievability equations
directly in pure Python so the math is correct and deterministic regardless of
which `fsrs` package version (if any) is installed in the runtime.

History / why this is a direct implementation rather than a py-fsrs wrapper:
  The previous version imported `from fsrs import FSRS` and instantiated it, but
  (a) never actually called the library in any method — every result came from
  hand-rolled formulas — and (b) py-fsrs >= 4 renamed `FSRS` to `Scheduler`, so
  on the pinned `fsrs==6.0.0` the import raised ImportError and the library was
  silently unavailable. The hand-rolled subsequent-stability formula was also
  not the FSRS formula. This rewrite fixes the equations. A future task may wire
  py-fsrs's `Scheduler` (its Card-object API) on top of this — that change must
  be validated in the Python 3.11 container, since py-fsrs 6 cannot be installed
  on Python 3.9.

External API (preserved for FSM/storage callers):
  calculate_memory(stability, difficulty, rating, days_elapsed) -> (new_s, new_d)
  next_interval(stability) -> int days
  get_retention(stability, days_elapsed) -> float in [0, 1]

Rating: 1=Again, 2=Hard, 3=Good, 4=Easy.
"""
import math
import logging

logger = logging.getLogger(__name__)

# Forgetting curve constants (FSRS-4.5/5, fixed-decay form):
#   R(t, S) = (1 + FACTOR * t / S) ** DECAY
# DECAY = -0.5 and FACTOR = 19/81 make R = 0.9 exactly when t == S.
_DECAY = -0.5
_FACTOR = 19.0 / 81.0

# Default FSRS-5 weights (w0..w18).
#   w0-w3  initial stability per rating (Again, Hard, Good, Easy)
#   w4,w5  initial difficulty (base, rating sensitivity)
#   w6,w7  difficulty update (delta, mean-reversion toward D0(Easy))
#   w8-w10 stability growth on successful recall
#   w11-w14 post-lapse stability
#   w15    hard penalty (applied on Hard)
#   w16    easy bonus (applied on Easy)
#   w17,w18 short-term/same-day steps (unused here; we schedule in whole days)
_DEFAULT_WEIGHTS = [
    0.40255, 1.18385, 3.173, 15.69105,
    7.1949, 0.5345,
    1.4604, 0.0046,
    1.54575, 0.1192, 1.01925,
    1.9395, 0.11, 0.29605, 2.2698,
    0.2315, 2.9898,
    0.51655, 0.6621,
]


class FSRSEngine:
    def __init__(self, desired_retention: float = 0.9):
        """
        Args:
            desired_retention: Target recall probability used to pick intervals
                (default 0.9 = 90%).
        """
        self.desired_retention = desired_retention
        self.w = _DEFAULT_WEIGHTS
        logger.info("FSRS Engine initialized (FSRS-5 direct implementation).")

    # ---- difficulty helpers ------------------------------------------------

    def _initial_difficulty(self, rating: int) -> float:
        """FSRS-5 initial difficulty: D0(G) = w4 - e^(w5*(G-1)) + 1, clamped [1,10]."""
        d0 = self.w[4] - math.exp(self.w[5] * (rating - 1)) + 1
        return min(max(d0, 1.0), 10.0)

    # ---- public API --------------------------------------------------------

    def calculate_memory(self, stability, difficulty, rating, days_elapsed):
        """
        Compute new (stability, difficulty) after a review.

        Args:
            stability: Current stability in days (None for first review).
            difficulty: Current difficulty 1-10 (None for first review).
            rating: 1=Again, 2=Hard, 3=Good, 4=Easy.
            days_elapsed: Days since the last review.

        Returns:
            (new_stability, new_difficulty).
        """
        rating = max(1, min(4, int(rating)))
        try:
            if stability is None or difficulty is None:
                # First review: initial stability is a direct weight per rating.
                new_s = float(self.w[rating - 1])
                new_d = self._initial_difficulty(rating)
                return new_s, new_d

            if stability <= 0:
                raise ValueError("stability must be positive for a subsequent review")

            # --- Difficulty: linear-damped update + mean reversion to D0(Easy) ---
            delta_d = -self.w[6] * (rating - 3)
            damped = difficulty + delta_d * (10.0 - difficulty) / 9.0
            new_d = self.w[7] * self._initial_difficulty(4) + (1.0 - self.w[7]) * damped
            new_d = min(max(new_d, 1.0), 10.0)

            # Retrievability at review time drives the stability update.
            retrievability = self.get_retention(stability, days_elapsed)

            if rating == 1:
                # Post-lapse (forgetting) stability.
                new_s = (
                    self.w[11]
                    * (difficulty ** -self.w[12])
                    * ((stability + 1) ** self.w[13] - 1)
                    * math.exp((1.0 - retrievability) * self.w[14])
                )
                # Post-lapse stability should not exceed the prior stability.
                new_s = min(new_s, stability)
            else:
                # Stability increase on successful recall.
                hard_penalty = self.w[15] if rating == 2 else 1.0
                easy_bonus = self.w[16] if rating == 4 else 1.0
                new_s = stability * (
                    1
                    + math.exp(self.w[8])
                    * (11.0 - new_d)
                    * (stability ** -self.w[9])
                    * (math.exp((1.0 - retrievability) * self.w[10]) - 1)
                    * hard_penalty
                    * easy_bonus
                )

            new_s = max(new_s, 0.01)
            return new_s, new_d

        except (ValueError, ZeroDivisionError, OverflowError) as e:
            logger.error(f"FSRS calculation error: {e}", exc_info=True)
            return (stability or 1.0), (difficulty or 5.0)

    def next_interval(self, stability):
        """
        Days until the next review so that predicted retrievability equals
        desired_retention:  I = (S / FACTOR) * (R^(1/DECAY) - 1).
        """
        if stability is None or stability <= 0:
            return 1
        try:
            interval = (stability / _FACTOR) * (self.desired_retention ** (1.0 / _DECAY) - 1)
            return max(1, round(interval))
        except (ValueError, ZeroDivisionError, OverflowError):
            return max(1, round(stability))

    def get_retention(self, stability, days_elapsed):
        """Current retrievability R = (1 + FACTOR * t / S) ** DECAY, in [0, 1]."""
        if stability is None or stability <= 0:
            return 0.0
        if days_elapsed <= 0:
            return 1.0
        try:
            return (1 + _FACTOR * days_elapsed / stability) ** _DECAY
        except (ValueError, ZeroDivisionError, OverflowError):
            return 0.0
