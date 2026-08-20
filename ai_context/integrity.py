# SUMMARY: Integrity checks for generated AI context artifacts.

from __future__ import annotations


def _integrity_issue(
    issue_type: str,
    message: str,
    repair_protocol: list[str],
) -> dict[str, object]:
    return {
        "issue_type": issue_type,
        "message": message,
        "repair_protocol": repair_protocol,
        "recommended_next_command": "uv run python scripts/generate_ai_context.py --check",
    }


def build_integrity_report(
    service_registry: dict[str, dict[str, object]],
    dependency_registry: dict[str, dict[str, dict[str, object]]],
    router_modules: list[str],
    route_inventory: dict[str, dict[str, object]],
) -> dict[str, object]:
    errors: list[str] = []
    issues: list[dict[str, object]] = []
    checked_rules = [
        "router modules imported into router_registration must exist in route_inventory",
        "dependency aliases must reference existing getters",
        "dependency getters must reference known service keys",
        "route dependencies must reference known service keys",
    ]
    known_service_keys = set(service_registry.keys())
    route_modules_in_inventory = set(route_inventory.keys())

    for module_name in router_modules:
        if module_name not in route_modules_in_inventory:
            message = f"Router module '{module_name}' is imported in router_registration but missing from route_inventory."
            errors.append(message)
            issues.append(
                _integrity_issue(
                    issue_type="route_inventory_missing",
                    message=message,
                    repair_protocol=[
                        "Restore the missing router module or remove its registration import.",
                        "Regenerate AI context artifacts after the router inventory resolves again.",
                    ],
                )
            )

    for alias_name, alias_metadata in dependency_registry["aliases"].items():
        getter_name = str(alias_metadata["getter"])
        if getter_name not in dependency_registry["getters"]:
            message = f"Dependency alias '{alias_name}' references missing getter '{getter_name}'."
            errors.append(message)
            issues.append(
                _integrity_issue(
                    issue_type="dependency_getter_missing",
                    message=message,
                    repair_protocol=[
                        "Restore the missing getter in project/infrastructure/api/dependencies.py.",
                        "Keep the alias -> getter -> service_key chain aligned with registered services.",
                    ],
                )
            )

    for getter_name, getter_metadata in dependency_registry["getters"].items():
        service_key = str(getter_metadata["service_key"])
        if service_key not in known_service_keys:
            message = (
                f"Dependency getter '{getter_name}' references missing service key '{service_key}'."
            )
            errors.append(message)
            issues.append(
                _integrity_issue(
                    issue_type="service_key_missing",
                    message=message,
                    repair_protocol=[
                        "Restore the missing service registration or update the getter to the correct service key.",
                        "Re-run generate_ai_context after the wiring chain resolves cleanly.",
                    ],
                )
            )

    for module_name, route_metadata in route_inventory.items():
        for endpoint in route_metadata["endpoints"]:
            for dependency in endpoint["dependencies"]:
                service_key = str(dependency["service_key"])
                if service_key not in known_service_keys:
                    message = (
                        "Route dependency "
                        f"'{dependency['alias']}' in module '{module_name}' "
                        f"references missing service key '{service_key}'."
                    )
                    errors.append(message)
                    issues.append(
                        _integrity_issue(
                            issue_type="route_service_key_missing",
                            message=message,
                            repair_protocol=[
                                "Restore the missing service registration or update the dependency alias mapping.",
                                "Confirm the endpoint alias resolves through dependencies.py before regenerating artifacts.",
                            ],
                        )
                    )

    return {
        "status": "ok" if not errors else "error",
        "checked_rules": checked_rules,
        "errors": errors,
        "issues": issues,
    }
