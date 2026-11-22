"""Static compose and grading.toml rules from contract/runtime.md.

Each case starts from a compliant project and breaks exactly one rule, so a case
fails when a rule stops firing or starts firing on compliant input.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from contract_check.contract import load_static_rules

static_rules = load_static_rules()

VALID_COMPOSE = {
    "services": {
        "init": {
            "build": ".",
            "command": ["python", "-m", "app.load"],
            "volumes": ["library-data:/data"],
        },
        "api": {
            "build": {"context": ".", "dockerfile": "Dockerfile"},
            "command": ["sh", "-c", "echo $$HOME && exec python -m app.api"],
            "depends_on": {
                "init": {"condition": "service_completed_successfully"},
                "db": {"condition": "service_healthy"},
            },
            "environment": {
                "LIBRARY_CORPUS_PATH": "${LIBRARY_CORPUS_PATH}",
                "VOYAGE_API_KEY": "${VOYAGE_API_KEY:-}",
                "LOG_LEVEL": "info",
            },
            "volumes": ["library-data:/data", {"type": "tmpfs", "target": "/tmp"}],
        },
        "db": {
            "image": "postgres:17",
            "environment": {"POSTGRES_PASSWORD": "example"},
            "healthcheck": {"test": ["CMD", "pg_isready"], "interval": "2s"},
        },
        "eval": {
            "build": ".",
            "profiles": ["eval"],
            "depends_on": {"init": {"condition": "service_completed_successfully"}},
            "command": ["python", "-m", "app.eval"],
        },
    },
    "volumes": {"library-data": {}},
}

VALID_GRADING = """\
external_secrets = ["VOYAGE_API_KEY"]
egress_hosts = ["api.voyageai.com"]
search_deterministic = true
eval_deterministic = false
"""


def write_project(root: Path, compose: dict, grading: str = VALID_GRADING) -> Path:
    (root / "compose.yaml").write_text(yaml.safe_dump(compose, sort_keys=False))
    (root / "grading.toml").write_text(grading)
    return root


def violations_of(root: Path) -> list[tuple[str, str | None, str | None]]:
    return [(v.rule, v.service, v.key) for v in static_rules.check_project(root)]


def test_compliant_project_has_no_violations(tmp_path):
    assert violations_of(write_project(tmp_path, VALID_COMPOSE)) == []


def svc(name, **changes):
    def mutate(doc):
        doc["services"].setdefault(name, {}).update(changes)

    return mutate


def top(key, value):
    def mutate(doc):
        doc[key] = value

    return mutate


def drop_init_dependency(doc):
    del doc["services"]["api"]["depends_on"]["init"]
    del doc["services"]["eval"]["depends_on"]


def init_started_only(doc):
    doc["services"]["api"]["depends_on"]["init"] = {"condition": "service_started"}


def drop_eval_profile(doc):
    del doc["services"]["eval"]["profiles"]


def drop_api(doc):
    del doc["services"]["api"]


def api_build(**changes):
    def mutate(doc):
        doc["services"]["api"]["build"].update(changes)

    return mutate


def volume_driver_bind(doc):
    doc["volumes"]["library-data"] = {
        "driver": "local",
        "driver_opts": {"type": "none", "o": "bind", "device": "/home"},
    }


def volume_named(doc):
    doc["volumes"]["library-data"] = {"name": "an-existing-host-volume"}


def volume_external(doc):
    doc["volumes"]["library-data"] = {"external": True, "name": "someone-elses-volume"}


CASES = [
    ("privileged", svc("api", privileged=True), ("privileged", "api", "privileged")),
    (
        "privileged-hook",
        svc("api", post_start=[{"command": "id", "privileged": True}]),
        ("privileged", "api", "post_start"),
    ),
    ("cap_add", svc("api", cap_add=["NET_ADMIN"]), ("cap_add", "api", "cap_add")),
    ("devices", svc("api", devices=["/dev/fuse:/dev/fuse"]), ("devices", "api", "devices")),
    ("network_mode", svc("api", network_mode="host"), ("network_mode", "api", "network_mode")),
    ("service-networks-default", svc("api", networks=["default"]), ("networks", "api", "networks")),
    ("service-networks-own", svc("api", networks=["outside"]), ("networks", "api", "networks")),
    ("top-level-networks-external", top("networks", {"outside": {"external": True}}), ("networks", None, "networks")),
    ("pid", svc("api", pid="host"), ("pid", "api", "pid")),
    ("ipc", svc("api", ipc="host"), ("ipc", "api", "ipc")),
    ("userns_mode", svc("api", userns_mode="host"), ("userns_mode", "api", "userns_mode")),
    ("bind-short", svc("db", volumes=["./pgdata:/var/lib/postgresql/data"]), ("bind_mount", "db", "volumes")),
    (
        "bind-long",
        svc("db", volumes=[{"type": "bind", "source": "/etc", "target": "/host-etc"}]),
        ("bind_mount", "db", "volumes"),
    ),
    ("bind-via-volume-driver", volume_driver_bind, ("bind_mount", None, "volumes.library-data")),
    ("volumes-from-container", svc("db", volumes_from=["container:other"]), ("bind_mount", "db", "volumes_from")),
    (
        "docker-sock",
        svc("db", volumes=["/var/run/docker.sock:/var/run/docker.sock"]),
        ("docker_socket", "db", "volumes"),
    ),
    ("use-api-socket", svc("api", use_api_socket=True), ("docker_socket", "api", "use_api_socket")),
    ("build-context-parent", api_build(context=".."), ("build_context", "api", "build.context")),
    (
        "build-context-remote",
        api_build(context="https://github.com/example/app.git"),
        ("build_context", "api", "build.context"),
    ),
    (
        "build-additional-context",
        api_build(additional_contexts={"shared": "../shared"}),
        ("build_context", "api", "build.additional_contexts"),
    ),
    ("build-secrets", api_build(secrets=["token"]), ("build_secrets", "api", "build.secrets")),
    ("build-ssh", api_build(ssh=["default"]), ("build_secrets", "api", "build.ssh")),
    ("include-remote", top("include", ["https://example.com/compose.yaml"]), ("include_extends", None, "include")),
    ("include-outside-repo", top("include", ["../other/compose.yaml"]), ("include_extends", None, "include")),
    (
        "extends-remote",
        svc("api", extends={"file": "https://example.com/base.yaml", "service": "api"}),
        ("include_extends", "api", "extends"),
    ),
    ("interpolate-host-var", svc("api", environment={"HOME_DIR": "${HOME}"}), ("interpolation", "api", "environment")),
    ("interpolate-bare-var", svc("db", command="echo $USER"), ("interpolation", "db", "command")),
    (
        "environment-passthrough",
        svc("api", environment=["LOG_LEVEL=info", "AWS_SECRET_ACCESS_KEY"]),
        ("interpolation", "api", "environment"),
    ),
    ("build-arg-passthrough", api_build(args=["GITHUB_TOKEN"]), ("interpolation", "api", "build.args")),
    ("published-ports", svc("api", ports=["8000:8000"]), ("published_ports", "api", "ports")),
    ("reserved-probe", svc("probe", image="busybox"), ("reserved_service_name", "probe", None)),
    ("reserved-fake-llm", svc("fake-llm", image="busybox"), ("reserved_service_name", "fake-llm", None)),
    ("reserved-egress-proxy", svc("egress-proxy", image="busybox"), ("reserved_service_name", "egress-proxy", None)),
    ("reserved-hostname", svc("db", hostname="fake-llm"), ("reserved_service_name", "db", "hostname")),
    ("reserved-domainname", svc("db", domainname="Egress-Proxy.local"), ("reserved_service_name", "db", "domainname")),
    ("links", svc("api", links=["db:fake-llm"]), ("reserved_service_name", "api", "links")),
    ("init-without-dependency", drop_init_dependency, ("one_shot_dependency", "init", "depends_on")),
    ("init-started-condition", init_started_only, ("one_shot_dependency", "init", "depends_on")),
    ("eval-without-profile", drop_eval_profile, ("service_profiles", "eval", "profiles")),
    ("api-with-profile", svc("api", profiles=["dev"]), ("service_profiles", "api", "profiles")),
    ("api-missing", drop_api, ("required_service", "api", None)),
    ("container-name", svc("db", container_name="library-db"), ("container_name", "db", "container_name")),
    (
        "logging-driver",
        svc("api", logging={"driver": "gelf", "options": {"gelf-address": "udp://203.0.113.5:12201"}}),
        ("host_access", "api", "logging"),
    ),
    ("security-opt", svc("api", security_opt=["seccomp:unconfined"]), ("host_access", "api", "security_opt")),
    ("env-file-outside-repo", svc("api", env_file=["/srv/secrets/.env"]), ("host_access", "api", "env_file")),
    ("external-volume", volume_external, ("host_access", None, "volumes.library-data")),
    ("named-host-volume", volume_named, ("host_access", None, "volumes.library-data")),
    (
        "extra-hosts",
        svc("api", extra_hosts=["host.docker.internal:host-gateway"]),
        ("host_access", "api", "extra_hosts"),
    ),
    ("dns", svc("api", dns=["203.0.113.53"]), ("host_access", "api", "dns")),
    ("dns-search", svc("api", dns_search=["example.com"]), ("host_access", "api", "dns_search")),
    (
        "gpu-reservation",
        svc(
            "api",
            deploy={
                "resources": {"reservations": {"devices": [{"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}]}}
            },
        ),
        ("host_access", "api", "deploy.resources.reservations.devices"),
    ),
    (
        "security-opt-mixed",
        svc("api", security_opt=["no-new-privileges", "apparmor:unconfined"]),
        ("host_access", "api", "security_opt"),
    ),
    ("logging-without-driver", svc("api", logging={"options": {"max-size": "10m"}}), ("host_access", "api", "logging")),
    ("top-level-models", top("models", {"llm": {"model": "ai/smollm2"}}), ("top_level_key", None, "models")),
]


@pytest.mark.parametrize("case_id,mutate,expected", CASES, ids=[c[0] for c in CASES])
def test_each_rule_reports_exactly_one_violation(tmp_path, case_id, mutate, expected):
    doc = copy.deepcopy(VALID_COMPOSE)
    mutate(doc)
    assert violations_of(write_project(tmp_path, doc)) == [expected]


ALLOWED_VALUES = [
    ("no-new-privileges", svc("api", security_opt=["no-new-privileges"])),
    ("no-new-privileges-true", svc("api", security_opt=["no-new-privileges:true"])),
    ("json-file-logging", svc("api", logging={"driver": "json-file", "options": {"max-size": "10m"}})),
    ("cpu-memory-limits", svc("api", deploy={"resources": {"limits": {"cpus": "2", "memory": "2g"}}})),
]


@pytest.mark.parametrize("case_id,mutate", ALLOWED_VALUES, ids=[c[0] for c in ALLOWED_VALUES])
def test_container_local_settings_are_allowed(tmp_path, case_id, mutate):
    doc = copy.deepcopy(VALID_COMPOSE)
    mutate(doc)
    assert violations_of(write_project(tmp_path, doc)) == []


def test_local_include_inside_repo_is_checked_too(tmp_path):
    doc = copy.deepcopy(VALID_COMPOSE)
    doc["include"] = ["deploy/extra.yaml"]
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "extra.yaml").write_text(
        yaml.safe_dump({"services": {"cache": {"image": "redis:7", "networks": ["default"]}}})
    )
    found = static_rules.check_project(write_project(tmp_path, doc))
    assert [(v.rule, v.service, v.key, v.file) for v in found] == [
        ("networks", "cache", "networks", "deploy/extra.yaml")
    ]


GRADING_CASES = [
    ("ip-literal", 'egress_hosts = ["10.0.0.1"]', "egress_hosts"),
    ("wildcard", 'egress_hosts = ["*.voyageai.com"]', "egress_hosts"),
    ("scheme", 'egress_hosts = ["https://api.voyageai.com"]', "egress_hosts"),
    ("port", 'egress_hosts = ["api.voyageai.com:443"]', "egress_hosts"),
    ("reserved-secret-name", 'external_secrets = ["VOYAGE_API_KEY", "ANTHROPIC_AUTH_TOKEN"]', "external_secrets"),
    ("secret-steers-docker", 'external_secrets = ["VOYAGE_API_KEY", "DOCKER_HOST"]', "external_secrets"),
    ("secret-steers-buildx", 'external_secrets = ["VOYAGE_API_KEY", "BUILDX_BUILDER"]', "external_secrets"),
    ("secret-steers-compose", 'external_secrets = ["VOYAGE_API_KEY", "COMPOSE_FILE"]', "external_secrets"),
    ("secret-named-path", 'external_secrets = ["VOYAGE_API_KEY", "PATH"]', "external_secrets"),
    ("secret-named-home", 'external_secrets = ["VOYAGE_API_KEY", "HOME"]', "external_secrets"),
]


@pytest.mark.parametrize("case_id,line,key", GRADING_CASES, ids=[c[0] for c in GRADING_CASES])
def test_grading_toml_rejects_invalid_values(tmp_path, case_id, line, key):
    field = line.split(" =")[0]
    grading = "\n".join(
        line if existing.startswith(field + " =") else existing for existing in VALID_GRADING.splitlines()
    )
    assert violations_of(write_project(tmp_path, VALID_COMPOSE, grading)) == [("grading_toml", None, key)]


def test_grading_toml_requires_all_four_keys(tmp_path):
    grading = 'external_secrets = ["VOYAGE_API_KEY"]\negress_hosts = []\nsearch_deterministic = true\n'
    assert violations_of(write_project(tmp_path, VALID_COMPOSE, grading)) == [
        ("grading_toml", None, "eval_deterministic")
    ]


def test_offline_project_needs_no_secrets_or_hosts(tmp_path):
    grading = "external_secrets = []\negress_hosts = []\nsearch_deterministic = true\neval_deterministic = true\n"
    doc = copy.deepcopy(VALID_COMPOSE)
    del doc["services"]["api"]["environment"]["VOYAGE_API_KEY"]
    assert violations_of(write_project(tmp_path, doc, grading)) == []


# Output of `docker compose config --format json` for a compliant project: Compose
# adds the `default` network and derives volume names from the project name.
RESOLVED_CONFIG = {
    "name": "demo",
    "networks": {"default": {"name": "demo_default", "ipam": {}}},
    "services": {
        "api": {
            "build": {"context": "{root}", "dockerfile": "Dockerfile"},
            "depends_on": {"init": {"condition": "service_completed_successfully", "required": True}},
            "networks": {"default": None},
            "volumes": [{"type": "volume", "source": "library-data", "target": "/data", "volume": {}}],
        },
        "init": {"build": {"context": "{root}", "dockerfile": "Dockerfile"}, "networks": {"default": None}},
        "eval": {"image": "python:3.12-slim", "profiles": ["eval"], "networks": {"default": None}},
    },
    "volumes": {"library-data": {"name": "demo_library-data"}},
}


def resolved_violations(root: Path, mutate=None) -> list[tuple[str, str | None, str | None]]:
    config = yaml.safe_load(yaml.safe_dump(RESOLVED_CONFIG).replace("{root}", str(root)))
    if mutate:
        mutate(config)
    found = static_rules.check_resolved_config(root, config, static_rules.HARNESS_VARIABLES)
    return [(v.rule, v.service, v.key) for v in found]


RESOLVED_CASES = [
    ("compliant", None, []),
    (
        "build-context-outside",
        lambda c: c["services"]["api"]["build"].update(context="/etc"),
        [("build_context", "api", "build.context")],
    ),
    (
        "bind-mount",
        lambda c: c["services"]["api"]["volumes"].append({"type": "bind", "source": "/home", "target": "/h"}),
        [("bind_mount", "api", "volumes")],
    ),
    (
        "second-network",
        lambda c: c["services"]["api"]["networks"].update(outside=None),
        [("networks", "api", "networks")],
    ),
    (
        "default-network-made-external",
        lambda c: c["networks"]["default"].update(external=True),
        [("networks", None, "networks")],
    ),
    (
        "volume-name-of-another-project",
        lambda c: c["volumes"]["library-data"].update(name="grade-other_library-data"),
        [("host_access", None, "volumes.library-data")],
    ),
]


@pytest.mark.parametrize("case_id,mutate,expected", RESOLVED_CASES, ids=[c[0] for c in RESOLVED_CASES])
def test_resolved_configuration_accepts_only_what_compose_adds(tmp_path, case_id, mutate, expected):
    assert resolved_violations(tmp_path, mutate) == expected


def test_empty_home_gets_only_the_callers_cli_plugins(tmp_path, monkeypatch):
    config_dir, extra, home = tmp_path / "docker-config", tmp_path / "extra", tmp_path / "home"
    (config_dir / "cli-plugins").mkdir(parents=True)
    extra.mkdir()
    (config_dir / "cli-plugins" / "docker-compose").write_text("compose")
    (extra / "docker-buildx").write_text("buildx")
    (config_dir / "config.json").write_text(f'{{"cliPluginsExtraDirs": ["{extra}"], "credsStore": "desktop"}}')
    home.mkdir()
    monkeypatch.setenv("DOCKER_CONFIG", str(config_dir))

    static_rules.link_docker_cli_plugins(home)

    plugins = home / ".docker" / "cli-plugins"
    assert sorted(p.name for p in (home / ".docker").iterdir()) == ["cli-plugins"]
    assert (plugins / "docker-compose").resolve() == (config_dir / "cli-plugins" / "docker-compose").resolve()
    assert (plugins / "docker-buildx").resolve() == (extra / "docker-buildx").resolve()


def test_no_user_plugins_leaves_the_home_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "missing"))
    static_rules.link_docker_cli_plugins(tmp_path / "home")
    assert not (tmp_path / "home").exists()
