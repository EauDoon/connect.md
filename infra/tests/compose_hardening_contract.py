"""Pure Compose hardening contract validation helpers."""

from __future__ import annotations

from typing import Any


def exact_field(
    service_name: str,
    service: dict,
    field: str,
    expected: Any,
    absent: object,
) -> None:
    if expected is absent:
        assert field not in service, (service_name, field)
        return
    assert service.get(field) == expected, (service_name, field, service.get(field))


def hardening_fields(service: dict, field_names: set[str]) -> dict:
    return {field: service[field] for field in field_names if field in service}


def validate_compose_hardening_contract(
    base_compose: dict,
    production_compose: dict,
    *,
    absent: object,
    compose_api_image: str,
    hardening_field_names: set[str],
    protected_runtime_override_fields: set[str],
    api_build_contract: dict,
    python_service_runtime_contracts: dict,
    public_service_pid_limits: dict,
    public_service_hardening_contracts: dict,
    excluded_base_hardening: dict,
) -> None:
    base_services = base_compose.get("services")
    production_services = production_compose.get("services")
    assert isinstance(base_services, dict)
    assert isinstance(production_services, dict)

    for service_name, runtime_contract in python_service_runtime_contracts.items():
        service = base_services.get(service_name)
        production_override = production_services.get(service_name)
        assert isinstance(service, dict), service_name
        assert isinstance(production_override, dict), service_name
        assert service.get("image") == compose_api_image, service_name
        exact_field(
            service_name,
            service,
            "build",
            api_build_contract if service_name == "api" else absent,
            absent,
        )
        exact_field(service_name, service, "entrypoint", absent, absent)
        assert service.get("user") == "10001:10001", service_name
        assert service.get("read_only") is True, service_name
        assert service.get("cap_drop") == ["ALL"], service_name
        assert service.get("cap_add", []) == [], service_name
        assert service.get("privileged", False) is False, service_name
        assert service.get("security_opt") == ["no-new-privileges:true"], service_name
        assert "volumes_from" not in service, service_name
        assert "devices" not in service, service_name
        for field, expected in runtime_contract.items():
            exact_field(service_name, service, field, expected, absent)
        if service_name in public_service_pid_limits:
            assert (
                service.get("pids_limit") == public_service_pid_limits[service_name]
            ), service_name
            assert (
                production_override.get("pids_limit")
                == public_service_pid_limits[service_name]
            ), service_name
        protected_overrides = (
            protected_runtime_override_fields & production_override.keys()
        )
        assert not protected_overrides, (service_name, protected_overrides)

    for service_name, expected_hardening in public_service_hardening_contracts.items():
        service = base_services.get(service_name)
        production_override = production_services.get(service_name)
        assert isinstance(service, dict), service_name
        assert isinstance(production_override, dict), service_name
        assert service.get("pids_limit") == public_service_pid_limits[service_name], (
            service_name
        )
        assert (
            production_override.get("pids_limit")
            == public_service_pid_limits[service_name]
        ), service_name
        for field, expected in expected_hardening.items():
            exact_field(service_name, service, field, expected, absent)
        if service_name == "frontend":
            assert service.get("image") == "connectmd-web:${CONNECTMD_IMAGE_TAG:-local}"
            assert service.get("build", {}).get("context") == "./apps/web"
        if service_name == "nginx":
            assert service.get("healthcheck", {}).get("test") == [
                "CMD-SHELL",
                "wget --no-verbose --spider http://127.0.0.1/nginx-health || exit 1",
            ]
        protected_overrides = (
            protected_runtime_override_fields & production_override.keys()
        )
        assert not protected_overrides, (service_name, protected_overrides)

    for service_name, expected_hardening in excluded_base_hardening.items():
        service = base_services.get(service_name)
        assert isinstance(service, dict), service_name
        assert hardening_fields(service, hardening_field_names) == expected_hardening, (
            service_name
        )

    for service_name in (*excluded_base_hardening, "certbot"):
        service = production_services.get(service_name)
        if service is not None:
            assert isinstance(service, dict), service_name
            assert hardening_fields(service, hardening_field_names) == {}, service_name
