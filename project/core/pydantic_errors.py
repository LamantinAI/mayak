# FILE: project/core/pydantic_errors.py
# SUMMARY: The pydantic error types whose message names no input value, so a log may carry it word for word.

from pydantic_core import ErrorDetails

# Listed by hand, not derived. The complement looked safe and was not: everything but value_error,
# assertion_error and a custom type still let through union_tag_invalid, which repeats the tag the
# caller sent, and a dozen parsing errors whose message ends in the parser's own words about the
# input — the independent check of 2026-09-25. A type pydantic adds later is left off a list like
# this one, so it is described by its type until someone reads its message and adds it here.
# tests/application/test_pydantic_errors.py holds each entry to its message template.
VALUE_FREE_ERROR_TYPES = frozenset(
    """
    missing extra_forbidden frozen_field frozen_instance invalid_key recursion_loop json_type
    none_required model_type model_attributes_type dataclass_type dataclass_exact_type
    default_factory_not_called greater_than greater_than_equal less_than less_than_equal
    multiple_of finite_number too_short too_long iterable_type string_type string_sub_type
    string_unicode string_too_short string_too_long string_pattern_mismatch string_not_ascii enum
    dict_type list_type tuple_type set_type set_item_not_hashable frozen_set_type bool_type
    bool_parsing int_type int_parsing int_parsing_size int_from_float float_type float_parsing
    bytes_type bytes_too_short bytes_too_long literal_error date_type date_from_datetime_inexact
    date_past date_future time_type datetime_type datetime_past datetime_future timezone_naive
    timezone_aware time_delta_type callable_type is_instance_of is_subclass_of arguments_type
    missing_argument unexpected_keyword_argument missing_keyword_only_argument
    unexpected_positional_argument missing_positional_only_argument multiple_argument_values
    url_type url_too_long url_scheme uuid_type uuid_version decimal_type decimal_parsing
    decimal_max_digits decimal_max_places decimal_whole_digits complex_type complex_str_parsing
    """.split()
)


# The message of one pydantic error when it names no input value, else None — the caller then
# describes the error by its type.
def value_free_message(item: ErrorDetails) -> str | None:
    return item["msg"] if item["type"] in VALUE_FREE_ERROR_TYPES else None
