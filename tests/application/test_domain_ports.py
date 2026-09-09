# FILE: tests/application/test_domain_ports.py
# SUMMARY: Smoke tests for canonical domain Protocol declarations to keep them in coverage.

from typing import Optional

import pytest

from project.domain.ports import LLMPort


# CLASS: tests.application.test_domain_ports.TestLLMPort
# SUMMARY: Verify the LLMPort Protocol can be implemented by a domain-pure class.
class TestLLMPort:
    # FUNCTION: test_protocol_can_be_implemented_and_called
    # SUMMARY: Ensure a class with the right structural shape satisfies LLMPort and can be invoked.
    @pytest.mark.unit
    async def test_protocol_can_be_implemented_and_called(self) -> None:
        # **LOGIC_STEP**: The keyword-only `system` is what makes this a conforming
        # implementation. If `LLMPort.call` gains a parameter like this and the fake does not
        # follow, the annotation below stops being satisfied — invisibly, because
        # `tests/application` is outside MYPY_TARGETS and a Protocol is not checked at runtime.
        # The port's own comment names this exact shape as the one that stops conforming.
        class _FakeLLM:
            async def call(self, prompt: str, *, system: Optional[str] = None) -> str:
                _ = system
                return f"echo:{prompt}"

        adapter: LLMPort = _FakeLLM()
        result = await adapter.call("hello")
        assert result == "echo:hello"
