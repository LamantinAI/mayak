# FILE: project/common/sampling.py
# SUMMARY: Decide whether one liveness-probe request should be logged, so an orchestrator polling
# /health every few seconds does not bury the log in identical lines.
#
# NOTE: This module used to be 240 lines: a `SamplingConfig` reading four rates from settings with
# an environment-variable fallback path, a `Sampler` singleton behind double-checked locking with a
# thread-local RNG, `get_config` / `update_config` for runtime adjustment, and five module-level
# convenience functions. Exactly one of them was ever called, from exactly one line of middleware —
# the rest was machinery for a configurability nobody used. What survives is the call site's actual
# need: one rate, one function.
#
# A project that grows a second sampled endpoint adds a rate here; adding it back is a few lines,
# and it will be shaped by a call site that exists rather than by one imagined in advance.

import random

from project.core.config import get_settings


# FUNCTION: should_sample_health_check
# SUMMARY: Report whether this health-check request should be logged.
# INPUT: custom_rate (float | None): Explicit rate for one call; None reads APP_SAMPLING_HEALTH_CHECK_RATE.
# OUTPUT: (bool): True when the request should be logged.
def should_sample_health_check(custom_rate: float | None = None) -> bool:
    # **LOGIC_STEP**: The rate is read per call rather than cached in a singleton. A health probe
    # arrives a few times a minute, so one settings lookup costs nothing measurable, and reading it
    # live means a test can change the setting without resetting global state — which is what the
    # old singleton forced.
    rate = (
        custom_rate
        if custom_rate is not None
        else get_settings().project.sampling_health_check_rate
    )
    rate = max(0.0, min(1.0, rate))

    # **LOGIC_STEP**: Short-circuit the two ends so 0.0 never logs and 1.0 always does, without
    # depending on how the RNG treats its boundaries.
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    return random.random() < rate  # nosec B311
