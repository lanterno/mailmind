"""How much of a nuisance a sender is, for ranking who to clean up first."""
import math

MIN_SCORE = 1.5


def noise_score(count: int, unseen: int, age_days: float) -> float:
    """More mail, more of it unread and more recent all score higher."""
    ratio = (unseen / count) if count else 0
    return count * (0.3 + 0.7 * ratio) * math.exp(-age_days / 30.0)
