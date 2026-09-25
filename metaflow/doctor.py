import os
import shutil
import sys
from dataclasses import dataclass
from enum import Enum

from metaflow import __version__ as METAFLOW_PACKAGE_VERSION
from metaflow.metaflow_config import (
    DEFAULT_DATASTORE,
    DEFAULT_METADATA,
    KUBERNETES_NAMESPACE,
    SERVICE_URL,
)
from metaflow.metaflow_version import get_version
from metaflow.plugins import DATASTORES, ENVIRONMENTS, METADATA_PROVIDERS
from metaflow.plugins.datastores.local_storage import LocalStorage


class DoctorStatus(str, Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


@dataclass
class DoctorCheck:
    name: str
    status: DoctorStatus
    message: str
    details: str = ""
    remediation: str = ""


def _status_symbol(status):
    return {
        DoctorStatus.HEALTHY: "✓",
        DoctorStatus.WARNING: "⚠",
        DoctorStatus.ERROR: "✗",
        DoctorStatus.UNAVAILABLE: "-",
    }.get(status, "?")


def format_diagnostic_report(checks):
    lines = []
    for check in checks:
        symbol = _status_symbol(check.status)
        label = check.name
        lines.append(f"{symbol} {label}: {check.message}")
        if check.details:
            lines.append(f"  {check.details}")
        if check.remediation:
            lines.append(f"  Fix: {check.remediation}")
    return "\n".join(lines)


def _is_inside_virtual_env():
    if os.environ.get("CONDA_PREFIX"):
        return True
    if os.environ.get("VIRTUAL_ENV"):
        return True
    return hasattr(sys, "real_prefix") or getattr(sys, "prefix", None) != getattr(
        sys, "base_prefix", None
    )


def get_default_datastore():
    return DEFAULT_DATASTORE


def get_default_metadata():
    return DEFAULT_METADATA


def get_service_url():
    return SERVICE_URL


def get_datastore_root(datastore):
    if datastore == "local":
        return LocalStorage.get_datastore_root_from_config(
            lambda *args, **kwargs: None, create_on_absent=False
        )
    config_map = {
        "s3": "DATASTORE_SYSROOT_S3",
        "azure": "DATASTORE_SYSROOT_AZURE",
        "gs": "DATASTORE_SYSROOT_GS",
        "spin": "DATASTORE_SYSROOT_SPIN",
    }
    if datastore not in config_map:
        return None
    from metaflow import metaflow_config

    return getattr(metaflow_config, config_map[datastore], None)


def check_python_version():
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info < (3, 8):
        return DoctorCheck(
            "python",
            DoctorStatus.WARNING,
            "Python %s is older than the current Metaflow recommendation." % version,
            details="Detected Python %s." % version,
            remediation="Upgrade to Python 3.8 or newer to avoid compatibility issues.",
        )
    return DoctorCheck(
        "python",
        DoctorStatus.HEALTHY,
        "Python %s is supported." % version,
        details="Detected Python %s." % version,
    )


def check_metaflow_version():
    version = get_version()
    if not version:
        return DoctorCheck(
            "metaflow",
            DoctorStatus.UNAVAILABLE,
            "Metaflow version could not be determined.",
            remediation="Ensure the active Python environment contains a valid Metaflow installation.",
        )
    return DoctorCheck(
        "metaflow",
        DoctorStatus.HEALTHY,
        "Metaflow %s is installed." % version,
        details="Package version: %s" % version,
    )


def check_datastore_configuration():
    datastore = get_default_datastore()
    if datastore == "local":
        root = get_datastore_root(datastore)
        if root is None:
            return DoctorCheck(
                "datastore",
                DoctorStatus.WARNING,
                "Local datastore is configured but no local Metaflow store was found.",
                remediation="Run 'metaflow configure' or initialize a local .metaflow directory in the project tree.",
            )
        return DoctorCheck(
            "datastore",
            DoctorStatus.HEALTHY,
            "Local datastore is available.",
            details="Store root: %s" % root,
        )

    if datastore not in {"s3", "azure", "gs", "spin"}:
        return DoctorCheck(
            "datastore",
            DoctorStatus.ERROR,
            "Unsupported datastore configuration: %s" % datastore,
            remediation="Set METAFLOW_DEFAULT_DATASTORE to a supported value such as local, s3, azure, or gs.",
        )

    root = get_datastore_root(datastore)
    if not root:
        name = {
            "s3": "METAFLOW_DATASTORE_SYSROOT_S3",
            "azure": "METAFLOW_DATASTORE_SYSROOT_AZURE",
            "gs": "METAFLOW_DATASTORE_SYSROOT_GS",
            "spin": "METAFLOW_DATASTORE_SYSROOT_SPIN",
        }[datastore]
        return DoctorCheck(
            "datastore",
            DoctorStatus.ERROR,
            "The %s datastore is configured, but its root is missing." % datastore,
            details="Missing configuration value: %s" % name,
            remediation="Set %s in your Metaflow config or environment." % name,
        )

    return DoctorCheck(
        "datastore",
        DoctorStatus.HEALTHY,
        "%s datastore is configured." % datastore,
        details="Store root: %s" % root,
    )


def check_metadata_provider_configuration():
    provider = get_default_metadata()
    if provider == "local":
        return DoctorCheck(
            "metadata",
            DoctorStatus.HEALTHY,
            "Local metadata provider is active.",
        )
    if provider == "spin":
        return DoctorCheck(
            "metadata",
            DoctorStatus.HEALTHY,
            "Spin metadata provider is active.",
        )
    if provider == "service":
        url = get_service_url()
        if not url:
            return DoctorCheck(
                "metadata",
                DoctorStatus.ERROR,
                "Metadata service is selected but no service URL is configured.",
                details="Default metadata provider: service",
                remediation="Set METAFLOW_SERVICE_URL or run 'metaflow configure' to initialize the service metadata provider.",
            )
        return DoctorCheck(
            "metadata",
            DoctorStatus.HEALTHY,
            "Metadata service is configured.",
            details="Metadata URL: %s" % url,
        )
    return DoctorCheck(
        "metadata",
        DoctorStatus.ERROR,
        "Unknown metadata provider: %s" % provider,
        remediation="Set METAFLOW_DEFAULT_METADATA to a supported provider such as local, service, or spin.",
    )


def check_aws_configuration():
    if DEFAULT_DATASTORE not in {"s3"}:
        return DoctorCheck(
            "aws",
            DoctorStatus.UNAVAILABLE,
            "AWS configuration is not required for the current datastore.",
            details="Default datastore: %s" % DEFAULT_DATASTORE,
        )

    try:
        import boto3
    except ImportError:
        return DoctorCheck(
            "aws",
            DoctorStatus.UNAVAILABLE,
            "boto3 is not installed.",
            remediation="Install AWS support with: python -m pip install boto3",
        )

    session = boto3.session.Session()
    credentials = session.get_credentials()
    if credentials is None:
        return DoctorCheck(
            "aws",
            DoctorStatus.ERROR,
            "No AWS credentials were detected for the active S3 datastore.",
            details="AWS credentials are required for accessing S3-backed Metaflow data.",
            remediation="Configure AWS via aws configure, AWS_PROFILE, or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY.",
        )

    region = session.region_name or os.environ.get("AWS_DEFAULT_REGION")
    if region is None:
        return DoctorCheck(
            "aws",
            DoctorStatus.WARNING,
            "AWS credentials are configured but no default region is set.",
            remediation="Set AWS_DEFAULT_REGION or configure the AWS region for your profile.",
        )

    return DoctorCheck(
        "aws",
        DoctorStatus.HEALTHY,
        "AWS credentials and region are configured.",
        details="Region: %s" % region,
    )


def check_kubernetes_configuration():
    kubectl = shutil.which("kubectl")
    namespace = KUBERNETES_NAMESPACE
    has_k8s_config = bool(namespace)
    if not has_k8s_config and kubectl is None:
        return DoctorCheck(
            "kubernetes",
            DoctorStatus.UNAVAILABLE,
            "Kubernetes checks are not applicable in this environment.",
            details="No Kubernetes namespace configuration found and kubectl is not installed.",
            remediation="Install kubectl or set METAFLOW_KUBERNETES_NAMESPACE when using Kubernetes workflows.",
        )
    if kubectl is None:
        return DoctorCheck(
            "kubernetes",
            DoctorStatus.WARNING,
            "Kubernetes is configured but the kubectl CLI is not installed.",
            details="Namespace: %s" % namespace,
            remediation="Install kubectl to validate Kubernetes access and cluster health.",
        )
    return DoctorCheck(
        "kubernetes",
        DoctorStatus.HEALTHY,
        "Kubernetes is configured and kubectl is available.",
        details="Namespace: %s" % namespace,
    )


def check_docker_availability():
    docker = shutil.which("docker")
    if docker is None:
        return DoctorCheck(
            "docker",
            DoctorStatus.UNAVAILABLE,
            "Docker is not installed or not on PATH.",
            remediation="Install Docker to run container-based workflows or confirm the binary is on PATH.",
        )
    return DoctorCheck(
        "docker",
        DoctorStatus.HEALTHY,
        "Docker is available.",
        details="Binary: %s" % docker,
    )


def check_dependency_manager():
    if os.environ.get("CONDA_PREFIX"):
        return DoctorCheck(
            "environment",
            DoctorStatus.HEALTHY,
            "Conda environment is active.",
            details="CONDA_PREFIX=%s" % os.environ.get("CONDA_PREFIX"),
        )
    if os.environ.get("VIRTUAL_ENV"):
        return DoctorCheck(
            "environment",
            DoctorStatus.HEALTHY,
            "Virtual environment is active.",
            details="VIRTUAL_ENV=%s" % os.environ.get("VIRTUAL_ENV"),
        )
    if _is_inside_virtual_env():
        return DoctorCheck(
            "environment",
            DoctorStatus.HEALTHY,
            "A Python virtual environment is active.",
            details="Python prefix: %s" % sys.prefix,
        )
    return DoctorCheck(
        "environment",
        DoctorStatus.WARNING,
        "No Conda or virtual environment was detected.",
        remediation="Create and activate a dedicated virtual environment or conda environment before installing Metaflow dependencies.",
    )


def check_plugins():
    plugin_names = []
    for plugin_group in (DATASTORES, METADATA_PROVIDERS, ENVIRONMENTS):
        for plugin in plugin_group:
            plugin_types = getattr(plugin, "TYPE", None)
            if plugin_types:
                plugin_names.append(plugin_types)
            else:
                plugin_names.append(getattr(plugin, "name", plugin.__class__.__name__))
    if not plugin_names:
        return DoctorCheck(
            "plugins",
            DoctorStatus.WARNING,
            "No Metaflow plugins were detected.",
            remediation="Install Metaflow plugins with the environment that is active in this Python interpreter.",
        )
    return DoctorCheck(
        "plugins",
        DoctorStatus.HEALTHY,
        "%d plugin(s) are available." % len(plugin_names),
        details=", ".join(sorted(set(plugin_names))),
    )


def run_diagnostics():
    checks = [
        check_python_version,
        check_metaflow_version,
        check_datastore_configuration,
        check_metadata_provider_configuration,
        check_aws_configuration,
        check_kubernetes_configuration,
        check_docker_availability,
        check_dependency_manager,
        check_plugins,
    ]
    results = []
    for check in checks:
        try:
            results.append(check())
        except Exception as exc:  # pragma: no cover - defensive boundary for new checks
            results.append(
                DoctorCheck(
                    check.__name__.replace("check_", "").replace("_", " "),
                    DoctorStatus.ERROR,
                    "Diagnostic check failed unexpectedly.",
                    details=str(exc),
                    remediation="Report this issue with the Metaflow diagnostic output and the active environment details.",
                )
            )
    return results


DIAGNOSTIC_CHECKS = [
    check_python_version,
    check_metaflow_version,
    check_datastore_configuration,
    check_metadata_provider_configuration,
    check_aws_configuration,
    check_kubernetes_configuration,
    check_docker_availability,
    check_dependency_manager,
    check_plugins,
]
