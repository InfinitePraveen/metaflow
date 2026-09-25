import sys

import pytest
from metaflow._vendor.click.testing import CliRunner

from metaflow.cmd.doctor_cmd import doctor
from metaflow.doctor import (
    DoctorCheck,
    DoctorStatus,
    check_aws_configuration,
    check_datastore_configuration,
    check_dependency_manager,
    check_docker_availability,
    check_kubernetes_configuration,
    check_metadata_provider_configuration,
    check_metaflow_version,
    check_plugins,
    check_python_version,
    format_diagnostic_report,
    run_diagnostics,
)


def test_successful_checks_are_marked_healthy(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 11, 9))
    result = check_python_version()

    assert result.status == DoctorStatus.HEALTHY
    assert "✓" in format_diagnostic_report([result])


def test_failed_checks_are_marked_error(monkeypatch):
    monkeypatch.setattr(
        "metaflow.doctor.get_default_datastore",
        lambda: "s3",
    )
    monkeypatch.setattr(
        "metaflow.doctor.get_datastore_root",
        lambda _: None,
    )

    result = check_datastore_configuration()

    assert result.status == DoctorStatus.ERROR
    assert "✗" in format_diagnostic_report([result])
    assert "METAFLOW_DATASTORE_SYSROOT_S3" in result.remediation


def test_warning_status_is_reported(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 7, 18))

    result = check_python_version()

    assert result.status == DoctorStatus.WARNING
    assert "⚠" in format_diagnostic_report([result])


def test_unavailable_optional_dependencies_are_indicated(monkeypatch):
    monkeypatch.setattr("metaflow.doctor.shutil.which", lambda name: None)

    result = check_docker_availability()

    assert result.status == DoctorStatus.UNAVAILABLE
    assert "not applicable" in result.message.lower() or "not installed" in result.message.lower()


def test_cli_exit_code_is_non_zero_for_errors(monkeypatch):
    monkeypatch.setattr(
        "metaflow.cmd.doctor_cmd.run_diagnostics",
        lambda: [DoctorCheck("Cloud", DoctorStatus.ERROR, "cloud config missing")],
    )

    result = CliRunner().invoke(doctor, [])

    assert result.exit_code == 1
    assert "✗" in result.output


def test_malformed_configuration_is_error(monkeypatch):
    monkeypatch.setattr("metaflow.doctor.get_default_metadata", lambda: "service")
    monkeypatch.setattr("metaflow.doctor.get_service_url", lambda: None)

    result = check_metadata_provider_configuration()

    assert result.status == DoctorStatus.ERROR
    assert "SERVICE_URL" in result.remediation


def test_missing_kubernetes_and_docker_are_reported(monkeypatch):
    monkeypatch.setattr("metaflow.doctor.shutil.which", lambda name: None)

    k8s = check_kubernetes_configuration()
    docker = check_docker_availability()

    assert k8s.status in {DoctorStatus.WARNING, DoctorStatus.UNAVAILABLE}
    assert docker.status == DoctorStatus.UNAVAILABLE


def test_dependency_manager_check_detects_virtualenv(monkeypatch):
    monkeypatch.setenv("CONDA_PREFIX", "/tmp/conda")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    result = check_dependency_manager()

    assert result.status == DoctorStatus.HEALTHY
    assert "conda" in result.message.lower()


def test_plugin_listing_does_not_fail(monkeypatch):
    result = check_plugins()

    assert result.status in {DoctorStatus.HEALTHY, DoctorStatus.WARNING}
    assert result.name == "plugins"


def test_custom_datastore_plugins_are_accepted(monkeypatch):
    monkeypatch.setattr("metaflow.doctor.get_default_datastore", lambda: "custom")
    monkeypatch.setattr(
        "metaflow.doctor.DATASTORES",
        [type("CustomStore", (), {"TYPE": "custom"})],
    )

    result = check_datastore_configuration()

    assert result.status == DoctorStatus.HEALTHY
    assert result.message == "custom datastore is configured."


def test_custom_metadata_providers_are_accepted(monkeypatch):
    monkeypatch.setattr("metaflow.doctor.get_default_metadata", lambda: "custom")
    monkeypatch.setattr(
        "metaflow.doctor.METADATA_PROVIDERS",
        [type("CustomProvider", (), {"TYPE": "custom"})],
    )

    result = check_metadata_provider_configuration()

    assert result.status == DoctorStatus.HEALTHY
    assert "custom" in result.message.lower()


def test_aws_sandbox_mode_is_not_reported_as_missing_credentials(monkeypatch):
    monkeypatch.setattr("metaflow.doctor.DEFAULT_DATASTORE", "s3")
    monkeypatch.setattr("metaflow.doctor.AWS_SANDBOX_ENABLED", True)

    result = check_aws_configuration()

    assert result.status == DoctorStatus.HEALTHY
    assert "sandbox" in result.message.lower()


def test_default_kubernetes_namespace_is_not_warned_when_not_configured(monkeypatch):
    monkeypatch.setattr("metaflow.doctor.KUBERNETES_NAMESPACE", "default")
    monkeypatch.delenv("METAFLOW_KUBERNETES_NAMESPACE", raising=False)
    monkeypatch.setattr("metaflow.doctor.shutil.which", lambda name: None)

    result = check_kubernetes_configuration()

    assert result.status == DoctorStatus.UNAVAILABLE
