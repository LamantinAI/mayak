# FILE: project/application/core_model.py
# SUMMARY: Shared Pydantic base model for application DTOs with strict default validation settings.

from pydantic import AliasGenerator, BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


# CLASS: project.application.core_model.CoreModel
# SUMMARY: Base DTO model that centralizes the default strict Pydantic configuration for application payloads.
class CoreModel(BaseModel):
    # ATTRIBUTE: model_config (ConfigDict)
    # SUMMARY: Default DTO configuration enforcing whitespace stripping, assignment validation, forbidden extras,
    #          and dual snake_case/camelCase field acceptance via validation alias generator.
    #          Responses are serialized in snake_case; inputs accept both snake_case and camelCase.
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
        alias_generator=AliasGenerator(validation_alias=to_camel),
        populate_by_name=True,
    )
