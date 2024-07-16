from abc import abstractmethod
from dataclasses import field
from typing import List, Literal, Optional, Sequence

import numpy as np
from pydantic.dataclasses import dataclass
from scipy.stats import norm  # type: ignore

from gbstats.messages import (
    BASELINE_VARIATION_ZERO_MESSAGE,
    ZERO_NEGATIVE_VARIANCE_MESSAGE,
    ZERO_SCALED_VARIATION_MESSAGE,
    NO_UNITS_IN_VARIATION_MESSAGE,
)
from gbstats.models.tests import BaseABTest, BaseConfig, TestResult, Uplift
from gbstats.models.statistics import (
    TestStatistic,
    BanditStatistic,
)
from gbstats.frequentist.tests import frequentist_diff, frequentist_variance
from gbstats.utils import truncated_normal_mean


# Configs
@dataclass
class GaussianPrior:
    mean: float = 0
    variance: float = 1
    proper: bool = False


@dataclass
class BayesianConfig(BaseConfig):
    inverse: bool = False
    alpha: float = 0.05
    prior_type: Literal["relative", "absolute"] = "relative"


@dataclass
class EffectBayesianConfig(BayesianConfig):
    prior_effect: GaussianPrior = field(default_factory=GaussianPrior)


@dataclass
class BanditConfig(BayesianConfig):
    bandit_weights_seed: int = 1
    top_two: bool = True
    prior_distribution: GaussianPrior = field(default_factory=GaussianPrior)


@dataclass
class BanditWeights:
    update_message: str
    weights: List[float] | None


# Results
RiskType = Literal["absolute", "relative"]


@dataclass
class BayesianTestResult(TestResult):
    chance_to_win: float
    risk: List[float]
    risk_type: RiskType
    error_message: Optional[str] = None


class BayesianABTest(BaseABTest):
    def __init__(
        self,
        stat_a: TestStatistic,
        stat_b: TestStatistic,
        config: BayesianConfig = BayesianConfig(),
    ):
        super().__init__(stat_a, stat_b)
        self.alpha = config.alpha
        self.inverse = config.inverse
        self.relative = config.difference_type == "relative"
        self.scaled = config.difference_type == "scaled"
        self.traffic_proportion_b = config.traffic_proportion_b
        self.phase_length_days = config.phase_length_days

    @abstractmethod
    def compute_result(self) -> BayesianTestResult:
        pass

    def _default_output(
        self, error_message: Optional[str] = None
    ) -> BayesianTestResult:
        """Return uninformative output when AB test analysis can't be performed
        adequately
        """
        return BayesianTestResult(
            chance_to_win=0.5,
            expected=0,
            ci=[0, 0],
            uplift=Uplift(dist="normal", mean=0, stddev=0),
            risk=[0, 0],
            error_message=error_message,
            risk_type="relative" if self.relative else "absolute",
        )

    def has_empty_input(self):
        return self.stat_a.n == 0 or self.stat_b.n == 0

    def credible_interval(
        self, mean_diff: float, std_diff: float, alpha: float
    ) -> List[float]:
        ci = norm.ppf([alpha / 2, 1 - alpha / 2], mean_diff, std_diff)
        return ci.tolist()

    def chance_to_win(self, mean_diff: float, std_diff: float) -> float:
        if self.inverse:
            return 1 - norm.sf(0, mean_diff, std_diff)  # type: ignore
        else:
            return norm.sf(0, mean_diff, std_diff)  # type: ignore

    def scale_result(
        self, result: BayesianTestResult, p: float, d: float
    ) -> BayesianTestResult:
        if result.uplift.dist != "normal":
            raise ValueError("Cannot scale relative results.")
        if p == 0:
            return self._default_output(ZERO_SCALED_VARIATION_MESSAGE)
        adjustment = self.stat_b.n / p / d
        return BayesianTestResult(
            chance_to_win=result.chance_to_win,
            expected=result.expected * adjustment,
            ci=[result.ci[0] * adjustment, result.ci[1] * adjustment],
            uplift=Uplift(
                dist=result.uplift.dist,
                mean=result.uplift.mean * adjustment,
                stddev=result.uplift.stddev * adjustment,
            ),
            risk=result.risk,
            risk_type=result.risk_type,
        )


class EffectBayesianABTest(BayesianABTest):
    def __init__(
        self,
        stat_a: TestStatistic,
        stat_b: TestStatistic,
        config: EffectBayesianConfig = EffectBayesianConfig(),
    ):
        super().__init__(stat_a, stat_b, config)
        # rescale prior if needed
        if self.relative and config.prior_type == "absolute":
            self.prior_effect = GaussianPrior(
                config.prior_effect.mean / abs(self.stat_a.unadjusted_mean),
                config.prior_effect.variance / pow(self.stat_a.unadjusted_mean, 2),
                config.prior_effect.proper,
            )
        elif not self.relative and config.prior_type == "relative":
            self.prior_effect = GaussianPrior(
                config.prior_effect.mean * abs(self.stat_a.unadjusted_mean),
                config.prior_effect.variance * pow(self.stat_a.unadjusted_mean, 2),
                config.prior_effect.proper,
            )
        else:
            self.prior_effect = config.prior_effect
        self.stat_a = stat_a
        self.stat_b = stat_b

    def compute_result(self):
        if (
            self.stat_a.mean == 0 or self.stat_a.unadjusted_mean == 0
        ) and self.relative:
            return self._default_output(BASELINE_VARIATION_ZERO_MESSAGE)
        if self.has_empty_input():
            return self._default_output(NO_UNITS_IN_VARIATION_MESSAGE)
        if self._has_zero_variance():
            return self._default_output(ZERO_NEGATIVE_VARIANCE_MESSAGE)

        data_variance = frequentist_variance(
            self.stat_a.variance,
            self.stat_a.unadjusted_mean,
            self.stat_a.n,
            self.stat_b.variance,
            self.stat_b.unadjusted_mean,
            self.stat_b.n,
            self.relative,
        )
        data_mean = frequentist_diff(
            self.stat_a.mean,
            self.stat_b.mean,
            self.relative,
            self.stat_a.unadjusted_mean,
        )

        post_prec = (
            1 / data_variance
            + int(self.prior_effect.proper) / self.prior_effect.variance
        )
        self.mean_diff = (
            (
                data_mean / data_variance
                + self.prior_effect.mean / self.prior_effect.variance
            )
            / post_prec
            if self.prior_effect.proper
            else data_mean
        )

        self.std_diff = np.sqrt(1 / post_prec)

        ctw = self.chance_to_win(self.mean_diff, self.std_diff)
        ci = self.credible_interval(self.mean_diff, self.std_diff, self.alpha)

        risk = self.get_risk(self.mean_diff, self.std_diff)
        # flip risk for inverse metrics
        risk = [risk[0], risk[1]] if not self.inverse else [risk[1], risk[0]]

        result = BayesianTestResult(
            chance_to_win=ctw,
            expected=self.mean_diff,
            ci=ci,
            uplift=Uplift(
                dist="normal",
                mean=self.mean_diff,
                stddev=self.std_diff,
            ),
            risk=risk,
            risk_type="relative" if self.relative else "absolute",
        )
        if self.scaled:
            result = self.scale_result(
                result, self.traffic_proportion_b, self.phase_length_days
            )
        return result

    @staticmethod
    def get_risk(mu, sigma) -> List[float]:
        prob_ctrl_is_better = norm.cdf(0.0, loc=mu, scale=sigma)
        mn_neg = truncated_normal_mean(mu=mu, sigma=sigma, a=-np.inf, b=0.0)
        mn_pos = truncated_normal_mean(mu=mu, sigma=sigma, a=0, b=np.inf)
        risk_ctrl = float((1.0 - prob_ctrl_is_better) * mn_pos)
        risk_trt = -float(prob_ctrl_is_better * mn_neg)
        return [risk_ctrl, risk_trt]


class Bandits:
    def __init__(
        self,
        stats: Sequence[BanditStatistic],
        config: BanditConfig,
    ):
        self.stats = stats
        self.config = config
        self.n_variations = len(stats)

    @property
    def bandit_weights_seed(self) -> int:
        return self.config.bandit_weights_seed

    @property
    def variation_means(self) -> np.ndarray:
        return np.array([arm.mean for arm in self.stats])

    @property
    def variation_variances(self) -> np.ndarray:
        return np.array([arm.variance for arm in self.stats])

    @property
    def variation_counts(self) -> np.ndarray:
        return np.array([arm.n for arm in self.stats])

    @property
    def prior_precision(self) -> np.ndarray:
        return np.full(
            (self.n_variations,),
            int(self.config.prior_distribution.proper)
            / self.config.prior_distribution.variance,
        )

    @property
    def data_precision(self) -> np.ndarray:
        return np.array(
            [
                float(n) / v if v > 0 else 0
                for n, v in zip(self.variation_counts, self.variation_variances)
            ]
        )

    @property
    def posterior_precision(self) -> np.ndarray:
        return self.prior_precision + self.data_precision

    @property
    def posterior_variance(self) -> np.ndarray:
        return 1 / self.posterior_precision

    @property
    def prior_mean(self) -> np.ndarray:
        return np.full((self.n_variations,), self.config.prior_distribution.mean)

    @property
    def posterior_mean(self) -> np.ndarray:
        return self.posterior_variance * (
            self.prior_precision * self.prior_mean
            + self.data_precision * self.variation_means
        )

    # number of Monte Carlo samples to perform when sampling to estimate weights for the SDK
    @property
    def n_samples(self):
        return int(1e4)

    # function that computes thompson sampling variation weights
    def compute_variation_weights(self) -> BanditWeights:
        min_n = 100
        if any(self.variation_counts < min_n):
            update_message = "some variation counts smaller than " + str(min_n)
            p = None
            return BanditWeights(update_message=update_message, weights=p)
        rng = np.random.default_rng(seed=self.bandit_weights_seed)
        y = rng.multivariate_normal(
            mean=self.posterior_mean,
            cov=np.diag(self.posterior_variance),
            size=self.n_samples,
        )
        if self.config.top_two:
            p = self.top_two_weights(y)
        else:
            row_maxes = np.max(y, axis=1)
            p = np.mean((y == row_maxes[:, np.newaxis]), axis=0)
        update_message = "successfully updated"
        return BanditWeights(update_message=update_message, weights=p.tolist())

    # function that takes weights for largest realization and turns into top two weights
    @staticmethod
    def top_two_weights(y) -> np.ndarray:
        """Calculates the proportion of times each column contains the largest or second largest element in a row.
        Args:
        arr: A 2D NumPy array.
        Returns:
        A NumPy array of proportions, one for each column.
        """
        # Get indices of sorted elements in each row
        sorted_indices = np.argsort(y, axis=1)
        # counts for number of times each variation was the largest
        unique_0, counts_0 = np.unique(
            sorted_indices[:, -1][:, np.newaxis], return_counts=True
        )
        # counts for number of times each variation was the second largest
        unique_1, counts_1 = np.unique(
            sorted_indices[:, -2][:, np.newaxis], return_counts=True
        )
        # put inside dicts and loop over count to ensure arms that are never the biggest are included
        dict_0 = dict(zip(unique_0, counts_0))
        dict_1 = dict(zip(unique_1, counts_1))
        n_variations = y.shape[1]
        final_counts = np.zeros((n_variations,))
        for i in range(n_variations):
            final_counts[i] = dict_0.get(i, 0) + dict_1.get(i, 0)
        return final_counts / sum(final_counts)

    # given n_periods x n_variations arrays of counts and means, what is the reward?
    @staticmethod
    def reward(variation_counts, variation_means) -> float:
        return np.sum(variation_counts * variation_means)

    # given n_periods x n_variations arrays of counts and means, what is the additional reward compared to fixed weight balanced design?
    @staticmethod
    def additional_reward(variation_counts, variation_means) -> float:
        # sample sizes per period
        period_counts = np.expand_dims(np.sum(variation_counts, axis=1), axis=1)
        n_variations = variation_counts.shape[1]
        variation_counts_balanced = np.tile(
            period_counts / n_variations, (1, n_variations)
        )
        counts_diff = variation_counts - variation_counts_balanced
        return np.sum(counts_diff * variation_means)
