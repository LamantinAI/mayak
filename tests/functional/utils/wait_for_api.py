# FILE: tests/functional/utils/wait_for_api.py
# SUMMARY: Utility to wait for the API to become ready.
# Relaxed functional-test helper module. Do not copy its patterns into project/** production code.

import backoff
import httpx

from utils.helpers import ServiceSettings


# SUMMARY: Checks if the API is ready to accept requests.
# INPUT: settings (ServiceSettings): Settings object containing host information.
# OUTPUT: (bool): True if API is ready, False otherwise.
def is_api_ready(settings: ServiceSettings) -> bool:
    # Attempt to call the health check endpoint.
    try:
        url = f"{settings.get_host()}/health/"
        response = httpx.get(url, timeout=5.0)
        # Return True if status code is 200.
        return response.status_code == 200
    except Exception as e:
        # Log failure and return False.
        print(f"API not ready yet: {e}")
        return False


# SUMMARY: Waits for API readiness with exponential backoff.
# INPUT: settings (ServiceSettings): Settings object containing host information.
# RAISES: Exception: If API connection fails after retries.
@backoff.on_exception(
    backoff.expo,
    Exception,
    max_time=60,
    max_tries=20,
    jitter=None,
)
def wait_for_api(settings: ServiceSettings) -> None:
    # Check if API is ready.
    if not is_api_ready(settings):
        # Raise exception to trigger backoff retry.
        raise Exception("API connection failed")


if __name__ == "__main__":
    # `utils.helpers`, not `helpers`. The bare name only resolved because the
    # script's own directory leads sys.path when it runs as `python3 utils/wait_for_api.py`, and
    # it is unresolvable for every other reader — mypy included. The tests service puts
    # /app/tests/functional on PYTHONPATH so both spellings are the same module.
    service_settings = ServiceSettings()
    print(f"Waiting for API at {service_settings.get_host()}...")
    wait_for_api(service_settings)
    print("API is ready!")
