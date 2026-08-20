# FILE: tests/application/test_sampling.py
# SUMMARY: Cover the health-check log sampler, which had no test of its own before.
# NOTE: The module it replaced was 240 lines with four rates, a locking singleton and a runtime
# reconfiguration API, and `find tests -iname '*sampl*'` matched nothing — the whole thing was
# unverified. What remains is small enough to pin exactly: the two ends must be absolute, because
# a rate of 0.0 that occasionally logs, or a 1.0 that occasionally does not, is the kind of defect
# nobody reports and nobody can reproduce.

from __future__ import annotations

import pytest

from project.common.sampling import should_sample_health_check
from project.core.config import clear_settings_override, set_settings_override
from tests.conftest import _FixtureSettings as FixtureSettings


# CLASS: tests.application.test_sampling.TestHealthCheckSampling
# SUMMARY: Verify the rate boundaries and the settings lookup behind them.
class TestHealthCheckSampling:
    # FUNCTION: test_zero_never_samples
    # SUMMARY: Verify a rate of 0.0 suppresses every request, not almost every one.
    @pytest.mark.unit
    def test_zero_never_samples(self) -> None:
        assert not any(should_sample_health_check(0.0) for _ in range(200))

    # FUNCTION: test_one_always_samples
    # SUMMARY: Verify a rate of 1.0 logs every request.
    @pytest.mark.unit
    def test_one_always_samples(self) -> None:
        assert all(should_sample_health_check(1.0) for _ in range(200))

    # FUNCTION: test_rate_is_clamped_into_range
    # SUMMARY: Verify values outside 0..1 are clamped rather than passed to the RNG.
    @pytest.mark.unit
    @pytest.mark.parametrize(("rate", "expected"), [(-5.0, False), (5.0, True)])
    def test_rate_is_clamped_into_range(self, rate: float, expected: bool) -> None:
        assert should_sample_health_check(rate) is expected

    # FUNCTION: test_settings_supply_the_rate_when_no_argument_is_given
    # SUMMARY: Verify the configured rate is read per call, so a changed setting takes effect.
    @pytest.mark.unit
    @pytest.mark.parametrize(("configured", "expected"), [(0.0, False), (1.0, True)])
    def test_settings_supply_the_rate_when_no_argument_is_given(
        self,
        configured: float,
        expected: bool,
    ) -> None:
        settings = FixtureSettings()
        settings.project.sampling_health_check_rate = configured
        set_settings_override(settings)
        try:
            assert should_sample_health_check() is expected
        finally:
            clear_settings_override()

    # FUNCTION: test_middle_rate_produces_both_outcomes
    # SUMMARY: Verify a rate between the ends actually samples rather than picking one branch.
    @pytest.mark.unit
    def test_middle_rate_produces_both_outcomes(self) -> None:
        # **LOGIC_STEP**: 200 draws at 0.5; the chance of an all-True or all-False run is 2^-199.
        results = {should_sample_health_check(0.5) for _ in range(200)}

        assert results == {True, False}
