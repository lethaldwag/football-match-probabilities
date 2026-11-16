"""Prediction models: a Poisson goals model and a base-rate reference model.

Both take a frame that has ALREADY been filtered with features.known_before().
"""

import math
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor

from app.models import MARKETS

PROB_TOLERANCE = 1e-9


class ModelUnavailable(Exception):
    """Not enough data to produce a responsible estimate."""


def validate_probabilities(market: str, probs: dict[str, float]) -> dict[str, float]:
    """Raise ValueError unless `probs` is a valid distribution over the market's selections."""
    expected = MARKETS[market]
    if tuple(probs) != expected:
        raise ValueError(f"{market}: expected selections {expected}, got {tuple(probs)}")
    values = list(probs.values())
    if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in values):
        raise ValueError(f"{market}: probability outside [0, 1]: {probs}")
    if abs(sum(values) - 1.0) > 1e-6:
        raise ValueError(f"{market}: probabilities sum to {sum(values)}, not 1")
    return probs


def market_probabilities(home_rate: float, away_rate: float, max_goals: int = 10) -> dict[str, dict[str, float]]:
    """Derive market probabilities from independent Poisson goal rates."""
    if not (home_rate > 0 and away_rate > 0 and math.isfinite(home_rate) and math.isfinite(away_rate)):
        raise ValueError("goal rates must be positive and finite")
    goals = np.arange(max_goals + 1)

    def pmf(rate: float) -> np.ndarray:
        log_p = goals * math.log(rate) - rate - np.array([math.lgamma(g + 1) for g in goals])
        return np.exp(log_p)

    grid = np.outer(pmf(home_rate), pmf(away_rate))
    grid /= grid.sum()  # renormalise the truncated tail
    home = float(np.tril(grid, -1).sum())
    away = float(np.triu(grid, 1).sum())
    draw = float(np.trace(grid))
    total = goals[:, None] + goals[None, :]
    over = float(grid[total >= 3].sum())
    btts = float(grid[1:, 1:].sum())
    out = {
        "1X2": {"home": home, "draw": draw, "away": away},
        "OU25": {"over": over, "under": 1.0 - over},
        "BTTS": {"yes": btts, "no": 1.0 - btts},
    }
    for market, probs in out.items():
        clipped = {k: min(1.0, max(0.0, v)) for k, v in probs.items()}
        norm = sum(clipped.values())
        out[market] = validate_probabilities(market, {k: v / norm for k, v in clipped.items()})
    return out


@dataclass(frozen=True)
class PoissonParams:
    half_life_days: float = 365.0  # weight of a past match halves every this many days
    alpha: float = 0.003  # L2 shrinkage of team strengths towards the league average
    lookback_days: int = 4 * 365
    min_matches: int = 150  # below this the model reports "unavailable"
    max_goals: int = 10


class PoissonGoalsModel:
    """log(goals) = intercept + home advantage + attack(team) + defence(opponent).

    Fitted as a regularised Poisson GLM with exponentially time-decayed sample weights.
    Teams never seen in the training window get average strength (coefficient 0).
    """

    name = "poisson-goals"
    version = "1.0"
    description = (
        "Independent Poisson goals model. Team attack and defence strengths plus home advantage are "
        "estimated with a regularised Poisson regression on past results of the same competition, "
        "with recent matches weighted more heavily. Market probabilities are derived from the "
        "resulting score distribution. Hyperparameters are fixed defaults, not tuned for betting."
    )

    def __init__(self, params: PoissonParams | None = None):
        self.params = params or PoissonParams()
        self.n_train = 0

    def params_dict(self) -> dict:
        return asdict(self.params)

    def fit(self, known: pd.DataFrame, cutoff: datetime) -> "PoissonGoalsModel":
        p = self.params
        if len(known) < p.min_matches:
            raise ModelUnavailable(
                f"only {len(known)} past matches available before the data cutoff; at least {p.min_matches} needed"
            )
        teams = sorted(set(known["home_id"]) | set(known["away_id"]))
        self._index = {team: i for i, team in enumerate(teams)}
        n, t = len(known), len(teams)
        home_idx = known["home_id"].map(self._index).to_numpy()
        away_idx = known["away_id"].map(self._index).to_numpy()

        # Two rows per match: home side scoring, then away side scoring.
        x = np.zeros((2 * n, 1 + 2 * t))
        rows = np.arange(n)
        x[rows, 0] = 1.0
        x[rows, 1 + home_idx] = 1.0
        x[rows, 1 + t + away_idx] = 1.0
        x[n + rows, 1 + away_idx] = 1.0
        x[n + rows, 1 + t + home_idx] = 1.0
        y = np.concatenate([known["home_goals"].to_numpy(), known["away_goals"].to_numpy()]).astype(float)

        age_days = (pd.Timestamp(cutoff) - known["kickoff"]).dt.total_seconds().to_numpy() / 86400.0
        weights = np.power(0.5, np.clip(age_days, 0, None) / p.half_life_days)
        weights = np.concatenate([weights, weights])

        glm = PoissonRegressor(alpha=p.alpha, max_iter=500, tol=1e-7)
        glm.fit(x, y, sample_weight=weights)
        self._intercept = float(glm.intercept_)
        self._home = float(glm.coef_[0])
        self._attack = glm.coef_[1 : 1 + t]
        self._defence = glm.coef_[1 + t :]
        self.n_train = n
        return self

    def _coef(self, values: np.ndarray, team_id: int) -> float:
        i = self._index.get(team_id)
        return float(values[i]) if i is not None else 0.0

    def has_team(self, team_id: int) -> bool:
        return team_id in self._index

    def expected_goals(self, home_id: int, away_id: int) -> tuple[float, float]:
        home = math.exp(
            self._intercept + self._home + self._coef(self._attack, home_id) + self._coef(self._defence, away_id)
        )
        away = math.exp(self._intercept + self._coef(self._attack, away_id) + self._coef(self._defence, home_id))
        return home, away

    def predict(self, home_id: int, away_id: int) -> dict[str, dict[str, float]]:
        return market_probabilities(*self.expected_goals(home_id, away_id), max_goals=self.params.max_goals)

    @property
    def home_advantage(self) -> float:
        """Multiplier on expected goals for playing at home."""
        return math.exp(self._home)

    def strengths(self, team_id: int) -> tuple[float | None, float | None]:
        """(attack, defensive weakness) multipliers vs an average team; None if the team is unseen."""
        if not self.has_team(team_id):
            return None, None
        return math.exp(self._coef(self._attack, team_id)), math.exp(self._coef(self._defence, team_id))


class BaseRateModel:
    """Reference baseline: historical outcome frequencies, identical for every match."""

    name = "base-rate"
    version = "1.0"
    description = (
        "Reference baseline. Predicts the same probabilities for every match: the smoothed frequency "
        "of each outcome in past matches of the competition. Any useful model must beat this."
    )
    min_matches = 50
    lookback_days = 4 * 365

    def __init__(self):
        self.n_train = 0

    def params_dict(self) -> dict:
        return {"min_matches": self.min_matches, "lookback_days": self.lookback_days, "smoothing": 1}

    def fit(self, known: pd.DataFrame, cutoff: datetime) -> "BaseRateModel":
        if len(known) < self.min_matches:
            raise ModelUnavailable(
                f"only {len(known)} past matches available before the data cutoff; "
                f"at least {self.min_matches} needed"
            )
        hg, ag = known["home_goals"].to_numpy(), known["away_goals"].to_numpy()
        n = len(known)

        def smooth(counts: dict[str, int]) -> dict[str, float]:
            return {k: (v + 1) / (n + len(counts)) for k, v in counts.items()}

        over, btts = int(((hg + ag) >= 3).sum()), int(((hg > 0) & (ag > 0)).sum())
        self._probs = {
            "1X2": smooth({"home": int((hg > ag).sum()), "draw": int((hg == ag).sum()), "away": int((hg < ag).sum())}),
            "OU25": smooth({"over": over, "under": n - over}),
            "BTTS": smooth({"yes": btts, "no": n - btts}),
        }
        for market, probs in self._probs.items():
            validate_probabilities(market, probs)
        self.n_train = n
        return self

    def predict(self, home_id: int, away_id: int) -> dict[str, dict[str, float]]:
        return {market: dict(probs) for market, probs in self._probs.items()}


def outcomes(home_goals: int, away_goals: int) -> dict[str, str]:
    """The winning selection in each market for a final score."""
    return {
        "1X2": "home" if home_goals > away_goals else "draw" if home_goals == away_goals else "away",
        "OU25": "over" if home_goals + away_goals >= 3 else "under",
        "BTTS": "yes" if home_goals > 0 and away_goals > 0 else "no",
    }
