"""Locate and load the published contract files that sit next to this package."""

from __future__ import annotations

import importlib.util
import json
import sys
from functools import cache
from pathlib import Path
from types import ModuleType

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

OPENAPI_URI = "urn:library-contract:openapi"


@cache
def contract_dir() -> Path:
    """Return the nearest `contract/` directory above this package.

    The package lives at `contract-check/` in the candidate repository and at
    `candidate/contract-check/` in the grading repository; both resolve to the
    repository's single `contract/` directory.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contract"
        if (candidate / "static_rules.py").is_file():
            return candidate
    raise FileNotFoundError("contract/static_rules.py not found above the contract-check package")


@cache
def _load_contract_module(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"library_contract_{name}", contract_dir() / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_static_rules() -> ModuleType:
    """contract/static_rules.py: compose.yaml and grading.toml rules."""
    return _load_contract_module("static_rules")


def load_data_rules() -> ModuleType:
    """contract/data_rules.py: corpus and inventory rules."""
    return _load_contract_module("data_rules")


@cache
def openapi_document() -> dict:
    return yaml.safe_load((contract_dir() / "openapi.yaml").read_text(encoding="utf-8"))


@cache
def _registry() -> Registry:
    resource = Resource.from_contents(openapi_document(), default_specification=DRAFT202012)
    return Registry().with_resource(OPENAPI_URI, resource)


@cache
def component_validator(name: str) -> Draft202012Validator:
    """Validator for `#/components/schemas/<name>` of contract/openapi.yaml."""
    if name not in openapi_document()["components"]["schemas"]:
        raise KeyError(f"unknown schema component {name!r}")
    schema = {"$ref": f"{OPENAPI_URI}#/components/schemas/{name}"}
    return Draft202012Validator(schema, registry=_registry())


@cache
def schema_validator(file_name: str) -> Draft202012Validator:
    """Validator for a standalone `contract/<file_name>` JSON Schema."""
    schema = json.loads((contract_dir() / file_name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)
