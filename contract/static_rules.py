"""Static rules for a candidate's `compose.yaml` and `grading.toml`.

One module serves both the public `contract-check --compose-dir` command and the
grading harness, so a project that passes here passes the harness's static gate.
The rules are described in `contract/runtime.md` ("Forbidden in compose.yaml").

The checks run twice. First on the files as written, because the normalized output
of `docker compose config` adds an implicit `default` network to every service and
would hide an explicit `networks: [default]`. Then, with `resolve=True`, on the
project as Docker Compose resolves it (`docker compose config --format json` with an
empty environment), because interpolation defaults, merges and includes can produce a
value the files do not show, such as a build context of `${LIBRARY_CORPUS_PATH:-/etc}`.
Resolving makes Compose read every file the project references, so it is skipped when
the first pass already found a reference outside the repository.

Dependencies: Python 3.11+, PyYAML, jsonschema; Docker Compose for `resolve=True`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml
from jsonschema import Draft202012Validator

COMPOSE_FILE = "compose.yaml"
GRADING_FILE = "grading.toml"
RESOLVED = "docker compose config"

# Variables the grading environment sets for every candidate service.
HARNESS_VARIABLES = frozenset(
    {
        "LIBRARY_CORPUS_PATH",
        "LIBRARY_INVENTORY_PATH",
        "LIBRARY_QUERIES_PATH",
        "LIBRARY_RESULTS_DIR",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_MODEL",
    }
)
RESERVED_SERVICE_NAMES = frozenset({"probe", "fake-llm", "egress-proxy"})
ALLOWED_TOP_LEVEL_KEYS = frozenset({"name", "version", "services", "volumes", "configs", "secrets", "include"})
FORBIDDEN_SERVICE_KEYS = {
    "privileged": "privileged",
    "cap_add": "cap_add",
    "devices": "devices",
    "network_mode": "network_mode",
    "networks": "networks",
    "pid": "pid",
    "ipc": "ipc",
    "userns_mode": "userns_mode",
    "ports": "published_ports",
    "use_api_socket": "docker_socket",
    "container_name": "container_name",
    # Links add host names that could shadow a service of the grading environment.
    "links": "reserved_service_name",
    # Keys that reach the host through the Docker daemon rather than the container.
    "security_opt": "host_access",
    "cgroup": "host_access",
    "cgroup_parent": "host_access",
    "uts": "host_access",
    "device_cgroup_rules": "host_access",
    "gpus": "host_access",
    "runtime": "host_access",
    "provider": "host_access",
    "models": "host_access",
    "logging": "host_access",
    "external_links": "host_access",
    "extra_hosts": "host_access",
    "dns": "host_access",
    "dns_search": "host_access",
    "dns_opt": "host_access",
}
# Values of otherwise forbidden keys that keep everything inside the container.
ALLOWED_SECURITY_OPT = frozenset({"no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"})
ALLOWED_LOGGING_DRIVER = "json-file"
HOOK_KEYS = ("pre_start", "post_start", "pre_stop")
ONE_SHOT_NAME = re.compile(r"^init(-[a-z0-9][a-z0-9_.-]*)?$")
VOLUME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
VARIABLE_REFERENCE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)|([A-Za-z_][A-Za-z0-9_]*))")


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str
    service: str | None = None
    key: str | None = None
    file: str = COMPOSE_FILE

    def __str__(self) -> str:
        parts = [self.file]
        if self.service is not None:
            parts.append(f"service {self.service!r}")
        if self.key is not None:
            parts.append(f"key {self.key!r}")
        return f"[{self.rule}] {', '.join(parts)}: {self.message}"


def check_project(project_dir: str | Path, resolve: bool = False, project_name: str = "contract-check") -> list[Violation]:
    """Return every rule violation of the project rooted at `project_dir`.

    With `resolve=True` the rules also run on the output of `docker compose config`
    (see the module docstring); a violation found by both passes is reported once.
    """
    root = Path(project_dir).resolve()
    violations: list[Violation] = []
    grading = _load_grading(root, violations)
    allowed_variables = HARNESS_VARIABLES | frozenset(grading.get("external_secrets", []) or [])

    compose_path = root / COMPOSE_FILE
    if not compose_path.is_file():
        violations.append(Violation("missing_file", f"{COMPOSE_FILE} not found", file=COMPOSE_FILE))
        return violations

    checker = _ComposeChecker(root, allowed_variables, violations)
    services = checker.check_file(compose_path)
    if services is not None:
        _check_project_services(services, violations)
    if resolve and services is not None and not any(_reads_outside_repository(v) for v in violations):
        try:
            config = resolved_config(root, project_name)
        except ComposeConfigError as error:
            violations.append(Violation("invalid_compose", str(error), file=RESOLVED))
        else:
            violations += check_resolved_config(root, config, allowed_variables)
    return _without_duplicates(violations)


class ComposeConfigError(Exception):
    """`docker compose config` rejected the project."""


DOCKER_CLI_PLUGINS = ("docker-compose", "docker-buildx")


def link_docker_cli_plugins(home: str | Path) -> None:
    """Give an empty home the caller's Compose and Buildx CLI plugins, and nothing else.

    Docker Desktop on macOS installs CLI plugins only under `~/.docker/cli-plugins`, so
    `docker` run with an empty HOME has no `compose` and no `buildx`. Each plugin binary is
    linked on its own; no `config.json`, context or credential reaches the empty home.
    Plugins in the system directories need no link: the CLI finds them with any HOME.
    """
    config_dir = Path(os.environ.get("DOCKER_CONFIG") or Path.home() / ".docker")
    try:
        extra = json.loads((config_dir / "config.json").read_text()).get("cliPluginsExtraDirs") or []
    except (OSError, ValueError, AttributeError):
        extra = []
    # The CLI's own order: extra directories, then the user's plugin directory.
    search = [Path(d).expanduser() for d in extra if isinstance(d, str)] + [config_dir / "cli-plugins"]
    target = Path(home) / ".docker" / "cli-plugins"
    for name in DOCKER_CLI_PLUGINS:
        found = next(
            (d / f for d in search for f in (name, name + ".exe") if (d / f).is_file()),
            None,
        )
        if found is None:
            continue
        target.mkdir(parents=True, exist_ok=True)
        link = target / found.name
        try:
            if link.is_symlink() or link.exists():  # a reused home may hold a stale link
                link.unlink()
            link.symlink_to(found.resolve())
        except OSError:
            pass  # No symlinks here (Windows without the privilege): same as before.


def resolved_config(project_dir: str | Path, project_name: str) -> dict:
    """The project as Docker Compose resolves it, every profile included.

    Runs with an empty environment and an empty home directory, so interpolation sees
    no variable of the caller's shell and Compose reads no user configuration.
    """
    root = Path(project_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="compose-config-") as home:
        empty_env_file = Path(home) / "empty.vars"
        empty_env_file.write_text("")
        link_docker_cli_plugins(home)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home}
        if os.environ.get("DOCKER_HOST"):
            env["DOCKER_HOST"] = os.environ["DOCKER_HOST"]
        command = [
            "docker", "compose", "-p", project_name, "--project-directory", str(root),
            "-f", str(root / COMPOSE_FILE), "--env-file", str(empty_env_file), "--profile", "*",
            "config", "--format", "json",
        ]  # fmt: skip
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        message = result.stderr.strip().splitlines()[-1:] or ["no error message"]
        raise ComposeConfigError(f"docker compose config failed: {message[0]}")
    return json.loads(result.stdout)


def check_resolved_config(root: Path, config: dict, allowed_variables: frozenset[str]) -> list[Violation]:
    """Rules on the output of `docker compose config --format json`.

    Values are already interpolated, includes and `extends` are merged, relative
    paths are absolute, and Compose has added the project's `default` network and
    the default name of every project volume; those additions are accepted.
    """
    violations: list[Violation] = []
    project = str(config.get("name") or "")
    checker = _ComposeChecker(root, allowed_variables, violations, resolved_project=project)
    services = checker.check_document(config, RESOLVED, root) or {}
    _check_project_services(services, violations, file=RESOLVED)
    return violations


def _reads_outside_repository(violation: Violation) -> bool:
    """True for a reference that resolving the project would make Compose read."""
    if violation.rule == "include_extends":
        return True
    return violation.rule == "host_access" and str(violation.key).split(".")[0] in (
        "env_file", "label_file", "secrets", "configs",
    )  # fmt: skip


def _without_duplicates(violations: list[Violation]) -> list[Violation]:
    seen: set[tuple] = set()
    unique = []
    for violation in violations:
        identity = (violation.rule, violation.service, violation.key)
        if identity not in seen:
            seen.add(identity)
            unique.append(violation)
    return unique


def _load_grading(root: Path, violations: list[Violation]) -> dict[str, Any]:
    path = root / GRADING_FILE
    if not path.is_file():
        violations.append(Violation("missing_file", f"{GRADING_FILE} not found", file=GRADING_FILE))
        return {}
    try:
        grading = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        violations.append(Violation("grading_toml", f"cannot parse: {error}", file=GRADING_FILE))
        return {}
    schema_path = Path(__file__).with_name("grading.schema.json")
    validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
    for error in sorted(validator.iter_errors(grading), key=lambda e: list(e.absolute_path)):
        key = _grading_error_key(error)
        violations.append(Violation("grading_toml", error.message, key=key, file=GRADING_FILE))
    if not isinstance(grading.get("external_secrets"), list):
        grading["external_secrets"] = []
    grading["external_secrets"] = [s for s in grading["external_secrets"] if isinstance(s, str)]
    return grading


def _grading_error_key(error) -> str | None:
    if error.absolute_path:
        return str(error.absolute_path[0])
    if error.validator == "required":
        match = re.search(r"'([^']+)' is a required property", error.message)
        return match.group(1) if match else None
    if error.validator == "additionalProperties":
        match = re.search(r"\('([^']+)'", error.message)
        return match.group(1) if match else None
    return None


class _ComposeChecker:
    def __init__(
        self,
        root: Path,
        allowed_variables: frozenset[str],
        violations: list[Violation],
        resolved_project: str | None = None,
    ):
        self.root = root
        self.allowed_variables = allowed_variables
        self.violations = violations
        self.visited: set[Path] = set()
        # Set when checking resolved output: names Compose derives from the project name.
        self.resolved_project = resolved_project

    def add(self, rule: str, message: str, file: str, service: str | None = None, key: str | None = None):
        self.violations.append(Violation(rule, message, service=service, key=key, file=file))

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def local_file(self, base_dir: Path, reference: Any) -> Path | None:
        """Resolve a file reference; None when it is remote or outside the repository."""
        if not isinstance(reference, str) or _is_remote(reference):
            return None
        resolved = (base_dir / reference).resolve()
        return resolved if resolved.is_relative_to(self.root) else None

    def check_file(self, path: Path) -> dict[str, dict] | None:
        """Check one compose file; return its services plus those of local includes."""
        if path in self.visited:
            return {}
        self.visited.add(path)
        file = self.relative(path)
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as error:
            self.add("invalid_yaml", f"cannot parse: {error}", file)
            return None
        if doc is None:
            doc = {}
        if not isinstance(doc, dict):
            self.add("invalid_yaml", "top level must be a mapping", file)
            return None
        return self.check_document(doc, file, path.parent)

    def check_document(self, doc: dict, file: str, base_dir: Path) -> dict[str, dict]:
        resolved = self.resolved_project is not None
        if not resolved:
            self.check_interpolation(doc, file, service=None, key=None)
        for key in doc:
            if key == "networks" and resolved and self.only_default_network(doc[key]):
                continue
            if key == "networks":
                self.add(
                    "networks",
                    "top-level networks are forbidden; the grading environment attaches networks",
                    file,
                    key="networks",
                )
            elif key not in ALLOWED_TOP_LEVEL_KEYS and not str(key).startswith("x-"):
                self.add("top_level_key", f"top-level key {key!r} is not supported", file, key=str(key))

        self.check_top_level_volumes(doc.get("volumes"), file)
        for section in ("secrets", "configs"):
            self.check_top_level_files(doc.get(section), section, base_dir, file)

        services: dict[str, dict] = {}
        raw_services = doc.get("services") or {}
        if not isinstance(raw_services, dict):
            self.add("invalid_yaml", "services must be a mapping", file, key="services")
            raw_services = {}
        for name, service in raw_services.items():
            service = service if isinstance(service, dict) else {}
            self.check_service(str(name), service, base_dir, file)
            services[str(name)] = service

        for included in self.check_includes(doc.get("include"), base_dir, file):
            included_services = self.check_file(included)
            services.update(included_services or {})
        return services

    def only_default_network(self, networks: Any) -> bool:
        """Resolved output: just the `default` network Compose adds, as Compose names it."""
        if not isinstance(networks, dict) or set(networks) != {"default"}:
            return False
        spec = networks["default"] or {}
        extra = set(spec) - {"name", "ipam"}
        return not extra and spec.get("name", f"{self.resolved_project}_default") == f"{self.resolved_project}_default"

    def check_includes(self, include: Any, base_dir: Path, file: str) -> Iterator[Path]:
        if include is None:
            return
        entries = include if isinstance(include, list) else [include]
        for entry in entries:
            if isinstance(entry, dict):
                paths = entry.get("path")
                paths = paths if isinstance(paths, list) else [paths]
                extra = entry.get("env_file")
                extra = extra if isinstance(extra, list) else ([extra] if extra else [])
                project_directory = entry.get("project_directory")
                for reference in extra + ([project_directory] if project_directory else []):
                    if self.local_file(base_dir, reference) is None:
                        self.add(
                            "include_extends",
                            f"include reference {reference!r} must be inside the repository",
                            file,
                            key="include",
                        )
            else:
                paths = [entry]
            for reference in paths:
                target = self.local_file(base_dir, reference)
                if target is None or not target.is_file():
                    self.add(
                        "include_extends",
                        f"include {reference!r} must be a local file inside the repository",
                        file,
                        key="include",
                    )
                else:
                    yield target

    def check_top_level_volumes(self, volumes: Any, file: str):
        if not isinstance(volumes, dict):
            return
        for name, spec in volumes.items():
            if not isinstance(spec, dict):
                continue
            key = f"volumes.{name}"
            if spec.get("external"):
                self.add("host_access", "external volumes are forbidden; declare a project volume", file, key=key)
            elif "name" in spec and spec["name"] != f"{self.resolved_project}_{name}":
                self.add(
                    "host_access",
                    "a volume 'name' can attach a volume that already exists on the host; drop it",
                    file,
                    key=key,
                )
            elif spec.get("driver_opts") or spec.get("driver") not in (None, "local"):
                self.add(
                    "bind_mount", "volume driver options can mount host paths; use a plain named volume", file, key=key
                )

    def check_top_level_files(self, section: Any, name: str, base_dir: Path, file: str):
        if not isinstance(section, dict):
            return
        for item, spec in section.items():
            if not isinstance(spec, dict):
                continue
            key = f"{name}.{item}"
            if spec.get("external"):
                self.add("host_access", f"external {name} are forbidden", file, key=key)
            if "environment" in spec and spec["environment"] not in self.allowed_variables:
                self.add("interpolation", f"{name} read from host variable {spec['environment']!r}", file, key=key)
            if "file" in spec and self.local_file(base_dir, spec["file"]) is None:
                self.add("host_access", f"{name} file {spec['file']!r} must be inside the repository", file, key=key)

    def check_service(self, name: str, service: dict, base_dir: Path, file: str):
        def add(rule, message, key=None):
            self.add(rule, message, file, service=name, key=key)

        if name in RESERVED_SERVICE_NAMES:
            add("reserved_service_name", f"service name {name!r} is reserved for the grading environment")
        for key in ("hostname", "domainname"):
            value = service.get(key)
            if isinstance(value, str) and value.split(".", 1)[0].lower() in RESERVED_SERVICE_NAMES:
                add("reserved_service_name", f"{key} {value!r} names a service of the grading environment", key)

        for key, rule in FORBIDDEN_SERVICE_KEYS.items():
            if key not in service:
                continue
            if key == "networks" and self.resolved_project is not None and service[key] in ({"default": None}, {"default": {}}):
                continue
            if key == "privileged" and not service[key]:
                continue
            if key == "use_api_socket" and not service[key]:
                continue
            if key == "ports" and not service[key]:
                continue
            if key == "security_opt" and _only_allowed_security_opt(service[key]):
                continue
            if (
                key == "logging"
                and isinstance(service[key], dict)
                and service[key].get("driver") == ALLOWED_LOGGING_DRIVER
            ):
                continue
            add(rule, f"{key!r} is forbidden{_ALLOWED_HINT.get(key, '')}", key)

        if _dig(service, "deploy", "resources", "reservations", "devices"):
            add("host_access", "device reservations (GPUs) are forbidden", "deploy.resources.reservations.devices")

        for hook_key in HOOK_KEYS:
            hooks = service.get(hook_key) or []
            if any(isinstance(h, dict) and h.get("privileged") for h in hooks):
                add("privileged", "privileged lifecycle hooks are forbidden", hook_key)

        if service.get("image") is not None and service.get("build") is not None:
            add(
                "host_image",
                "'image' with 'build' tags the build with that name, replacing an image of the same name on the "
                "host; drop 'image'",
                "image",
            )
        if str(service.get("pull_policy") or "").lower() == "build":
            add(
                "host_image",
                "pull_policy: build makes 'docker compose up' build again, outside the grading build step; "
                "drop pull_policy",
                "pull_policy",
            )
        self.check_service_volumes(service, add)
        self.check_build(service.get("build"), base_dir, add)
        self.check_extends(service.get("extends"), base_dir, add)
        for key in ("env_file", "label_file"):
            refs = service.get(key)
            refs = refs if isinstance(refs, list) else ([refs] if refs else [])
            for ref in refs:
                ref = ref.get("path") if isinstance(ref, dict) else ref
                if self.local_file(base_dir, ref) is None:
                    add("host_access", f"{key} {ref!r} must be inside the repository", key)
        if self.resolved_project is not None:
            return  # values are already interpolated
        self.check_interpolation(service, file, service=name, key=None)
        if _has_passthrough(service.get("environment"), self.allowed_variables):
            add("interpolation", "environment entries without a value read the host environment", "environment")

    def check_service_volumes(self, service: dict, add):
        volumes = service.get("volumes") or []
        for volume in volumes if isinstance(volumes, list) else []:
            if isinstance(volume, str):
                source = volume.split(":", 1)[0] if ":" in volume else ""
                volume_type = "volume" if not source or VOLUME_NAME.match(source) else "bind"
            elif isinstance(volume, dict):
                source = str(volume.get("source") or "")
                volume_type = volume.get("type", "volume")
            else:
                continue
            if "docker.sock" in source:
                add("docker_socket", "mounting the Docker socket is forbidden", "volumes")
            elif volume_type == "image":
                add("host_image", f"mounting image {source!r} exposes images of the host; use a named volume", "volumes")
            elif volume_type == "bind":
                add("bind_mount", f"bind mount of host path {source!r} is forbidden; use a named volume", "volumes")
        for entry in service.get("volumes_from") or []:
            if isinstance(entry, str) and entry.startswith("container:"):
                add("bind_mount", "volumes_from a container outside the project is forbidden", "volumes_from")

    def check_build(self, build: Any, base_dir: Path, add):
        if build is None:
            return
        if isinstance(build, str):
            build = {"context": build}
        if not isinstance(build, dict):
            return
        context_ref = build.get("context", ".")
        context = self.local_file(base_dir, context_ref)
        if context is None:
            add(
                "build_context",
                f"build context {context_ref!r} must be a directory inside the repository",
                "build.context",
            )
        dockerfile = build.get("dockerfile")
        if context is not None and dockerfile and self.local_file(context, dockerfile) is None:
            add("build_context", f"dockerfile {dockerfile!r} must be inside the repository", "build.dockerfile")
        extra = build.get("additional_contexts") or {}
        if isinstance(extra, list):
            extra = dict(item.split("=", 1) for item in extra if isinstance(item, str) and "=" in item)
        for value in extra.values() if isinstance(extra, dict) else []:
            value = str(value)
            if value.startswith(("docker-image://", "service:")):
                continue
            target = value.removeprefix("oci-layout://")
            if self.local_file(base_dir, target) is None:
                add(
                    "build_context",
                    f"additional context {value!r} must be inside the repository",
                    "build.additional_contexts",
                )
        for key in ("secrets", "ssh"):
            if build.get(key):
                add("build_secrets", f"build.{key} is forbidden", f"build.{key}")
        if build.get("tags"):
            add("host_image", "build.tags can replace an image of the same name on the host", "build.tags")
        for key in ("cache_from", "cache_to"):
            entries = build.get(key) or []
            entries = entries if isinstance(entries, list) else [entries]
            # Buildx reads the keys and the type of a cache entry case-insensitively.
            if any("type=local" in str(entry).replace(" ", "").lower() for entry in entries):
                add("host_image", f"build.{key} with type=local reads or writes a host directory", f"build.{key}")
        if build.get("privileged"):
            add("privileged", "privileged builds are forbidden", "build.privileged")
        for key in ("network", "entitlements"):
            if build.get(key):
                add("host_access", f"build.{key} is forbidden", f"build.{key}")
        if self.resolved_project is None and _has_passthrough(build.get("args"), self.allowed_variables):
            add("interpolation", "build args without a value read the host environment", "build.args")

    def check_extends(self, extends: Any, base_dir: Path, add):
        if not isinstance(extends, dict) or "file" not in extends:
            return
        target = self.local_file(base_dir, extends["file"])
        if target is None or not target.is_file():
            add(
                "include_extends",
                f"extends file {extends['file']!r} must be a local file inside the repository",
                "extends",
            )
        else:
            self.check_file(target)

    def check_interpolation(self, node: Any, file: str, service: str | None, key: str | None):
        """Report `${VAR}` / `$VAR` references outside the allowed variables."""
        if service is not None and key is None and isinstance(node, dict):
            for child_key, child in node.items():
                self.check_interpolation(child, file, service, str(child_key))
            return
        if service is None and key is None and isinstance(node, dict):
            for child_key, child in node.items():
                if child_key == "services":
                    continue
                self.check_interpolation(child, file, None, str(child_key))
            return
        names = sorted(set(_referenced_variables(node)) - self.allowed_variables)
        if names:
            self.add(
                "interpolation",
                f"references host variable(s) {', '.join(names)}; only grading variables and "
                "external_secrets from grading.toml may be interpolated",
                file,
                service=service,
                key=key,
            )


def _check_project_services(services: dict[str, dict], violations: list[Violation], file: str = COMPOSE_FILE):
    def add(rule, message, service=None, key=None):
        violations.append(Violation(rule, message, service=service, key=key, file=file))

    for required in ("api", "eval"):
        if required not in services:
            add("required_service", f"service {required!r} is required", service=required)
    if "api" in services and services["api"].get("profiles"):
        add("service_profiles", "service 'api' must not declare profiles", "api", "profiles")
    if "eval" in services and services["eval"].get("profiles") != ["eval"]:
        add("service_profiles", "service 'eval' must declare profiles: [eval]", "eval", "profiles")

    for name in services:
        if not ONE_SHOT_NAME.match(name):
            continue
        conditions = []
        for other_name, other in services.items():
            depends_on = other.get("depends_on") or {}
            if isinstance(depends_on, list):
                depends_on = {item: {} for item in depends_on}
            if other_name != name and name in depends_on:
                spec = depends_on[name] or {}
                conditions.append(spec.get("condition", "service_started"))
        if not conditions or any(c != "service_completed_successfully" for c in conditions):
            add(
                "one_shot_dependency",
                f"one-shot service {name!r} must be required by other services with "
                "condition: service_completed_successfully",
                name,
                "depends_on",
            )


_ALLOWED_HINT = {
    "security_opt": " except exactly 'no-new-privileges'",
    "logging": " except 'driver: json-file'",
    "links": ": services already reach each other by service name",
}


def _dig(node: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _only_allowed_security_opt(options: Any) -> bool:
    return isinstance(options, list) and all(option in ALLOWED_SECURITY_OPT for option in options)


def _referenced_variables(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        text = node.replace("$$", "")
        for braced, bare in VARIABLE_REFERENCE.findall(text):
            yield braced or bare
    elif isinstance(node, dict):
        for value in node.values():
            yield from _referenced_variables(value)
    elif isinstance(node, list):
        for value in node:
            yield from _referenced_variables(value)


def _has_passthrough(entries: Any, allowed: frozenset[str]) -> bool:
    """True when a list/dict of KEY=VALUE entries has a key without a value."""
    if isinstance(entries, dict):
        return any(value is None and key not in allowed for key, value in entries.items())
    if isinstance(entries, list):
        return any(isinstance(item, str) and "=" not in item and item not in allowed for item in entries)
    return False


def _is_remote(reference: str) -> bool:
    return "://" in reference or reference.startswith("git@")
