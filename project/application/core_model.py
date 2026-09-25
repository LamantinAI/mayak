# FILE: project/application/core_model.py
# SUMMARY: Shared Pydantic base model for application DTOs with strict default validation settings.

from pydantic import AliasGenerator, BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CoreModel(BaseModel):
    # Responses are serialized in snake_case; inputs accept both snake_case and camelCase.
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
        alias_generator=AliasGenerator(validation_alias=to_camel),
        populate_by_name=True,
    )


# A tool's argument model is a schema the provider reads, not a payload a client sends, and
# langchain leaves out of that schema a field whose only alias is a validation alias. CoreModel's
# generator gives every field exactly that, so a tool whose arguments inherited it reached the model
# with no parameters at all — `"properties": {}` with langchain-core 1.5.3 — while mock mode, which
# never reads the schema, kept passing. Found by the bench2 measurement (2026-09-24).
# LLMService.bind_tools refuses such a tool for the same reason.
class ToolArgs(BaseModel):
    # CoreModel's settings without the alias generator and populate_by_name.
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )
