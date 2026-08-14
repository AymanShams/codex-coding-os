from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPO_ROOT
    / "capability-routing"
    / "deployment"
    / "promote_worker_runtime_bom.py"
)
SPEC = importlib.util.spec_from_file_location("promote_worker_runtime_bom", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
promotion = importlib.util.module_from_spec(SPEC)
import sys

sys.modules[SPEC.name] = promotion
SPEC.loader.exec_module(promotion)


SCHEMA = REPO_ROOT / "capability-routing" / "worker-runtime-bom.schema.json"
ANTIGRAVITY_FIXTURE_FILES = (
    "__init__.py",
    "config.py",
    "dependency_identity.py",
    "locking.py",
    "receipts.py",
    "route_authority.py",
    "runner.py",
    "runtime_identity.py",
    "server.py",
    "service.py",
    "source_integrity.py",
    "windows_job.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class WorkerRuntimeBomPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.local_app_data = self.root / "localappdata"
        self.local_app_data.mkdir()
        self.environment_patch = mock.patch.dict(
            os.environ,
            {"LOCALAPPDATA": str(self.local_app_data)},
        )
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)
        self.config = self.root / "config.toml"
        self.target = self.root / "capability-routing" / "worker-runtime-bom.json"
        self.candidate = self.root / "candidate.json"
        self.las = self.root / "las"
        self.antigravity = self.root / "antigravity"
        self.las.mkdir()
        self.antigravity.mkdir()
        self.las_command = self.las / ".venv" / "Scripts" / "python.exe"
        self.antigravity_command = (
            self.antigravity / ".venv" / "Scripts" / "python.exe"
        )
        self.agy = self.root / "agy.exe"
        for command in (self.las_command, self.antigravity_command):
            command.parent.mkdir(parents=True)
            command.write_bytes(b"test gateway")
        self.agy.write_bytes(b"test antigravity")
        self.base_python_home = self.root / "base-python"
        self.base_python_home.mkdir()
        self.base_python = self.base_python_home / "python.exe"
        self.base_python.write_bytes(b"test base Python")
        (self.base_python_home / "pythonw.exe").write_bytes(b"test base Python windowless")
        hermes_api = self.root / "hermes" / "api_server.py"
        hermes_metadata = self.root / "hermes" / "METADATA"
        hermes_api.parent.mkdir()
        hermes_api.write_bytes(b"hermes api")
        hermes_metadata.write_bytes(b"Version: 0.19.0\n")
        for path, payload in (
            (self.las / "pyproject.toml", b"[project]\nname='las'\n"),
            (self.las / "uv.lock", b"fixture\n"),
            (self.las / "vendor" / "versions.json", b"{}\n"),
            (self.las / "src" / "local_agent_stack" / "__init__.py", b"# las\n"),
            (self.las / "src" / "local_agent_stack" / "server.py", b"# server\n"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        for directory in (
            self.las / "config" / "schemas",
            self.las / "scripts",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        antigravity_package = self.antigravity / "src" / "antigravity_adapter"
        antigravity_package.mkdir(parents=True)
        (antigravity_package / "__init__.py").write_text(
            "# antigravity\n", encoding="utf-8"
        )
        self.python_closures = {
            "local-agent-stack": self._install_python_closure(
                self.las, self.las_command, "local_agent_stack"
            ),
            "antigravity-adapter": self._install_python_closure(
                self.antigravity,
                self.antigravity_command,
                "antigravity_adapter",
            ),
        }
        self.probe_patch = mock.patch.object(
            promotion,
            "_probe_python_execution",
            side_effect=self._python_probe,
        )
        self.probe_patch.start()
        self.addCleanup(self.probe_patch.stop)
        self.pth_probe_patch = mock.patch.object(
            promotion,
            "_probe_pth_import_origins",
            side_effect=self._pth_probe,
        )
        self.pth_probe_patch.start()
        self.addCleanup(self.pth_probe_patch.stop)
        hermes_identity = {
            "distribution_version": "0.19.0",
            "distribution_metadata_path": str(hermes_metadata),
            "distribution_metadata_sha256": sha256(hermes_metadata),
            "api_source_path": str(hermes_api),
            "api_source_sha256": sha256(hermes_api),
            "overlay_id": "test-hermes-overlay",
        }
        (self.las / "runtime-dependencies.lock.json").write_text(
            json.dumps(
                {
                    "schema_version": "local-agent-stack-runtime-dependencies-v2",
                    "release_id": "local-agent-stack-test",
                    "python_execution_closure": self.python_closures[
                        "local-agent-stack"
                    ],
                    "files": [],
                    "executables": {},
                    "ollama": {},
                    "hermes": hermes_identity,
                    "agent_memory": {},
                    "scheduler_contract": {},
                    "startup_receipts": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        las_source_paths = [
            self.las / "pyproject.toml",
            self.las / "uv.lock",
            self.las / "runtime-dependencies.lock.json",
            self.las / "vendor" / "versions.json",
            self.las / "src" / "local_agent_stack" / "__init__.py",
            self.las / "src" / "local_agent_stack" / "server.py",
        ]
        las_source_sha = promotion._source_inventory_sha256(
            self.las.resolve(), las_source_paths
        )
        (self.las / "runtime-identity.json").write_text(
            json.dumps(
                {
                    "schema_version": "local-agent-stack-runtime-identity-v2",
                    "component": "local-agent-stack",
                    "runtime_version": "0.2.0",
                    "release_id": "local-agent-stack-test",
                    "catalogue_router_compatibility": {
                        "route_schema_version": "3.0",
                        "route_registry_schema_version": 3,
                        "authority_pointer_schema_version": "capability-authority-pointer-v1",
                        "manifest_schema_versions": ["1.2", "1.3"],
                    },
                    "nested_dependencies": {
                        "hermes": {
                            "distribution_version": "0.19.0",
                            "overlay_id": "test-hermes-overlay",
                            "api_source_sha256": sha256(hermes_api),
                        }
                    },
                    "python_execution_closure": self.python_closures[
                        "local-agent-stack"
                    ],
                    "source_sha256": las_source_sha,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        agy_lock = {
            "schema_version": "antigravity-adapter-dependency-lock-v2",
            "python_execution_closure": self.python_closures[
                "antigravity-adapter"
            ],
            "agy": {
                "version": "1.1.13",
                "executable_sha256": sha256(self.agy),
                "model_efforts": {"gemini-3.1-pro-high": "high"},
            },
        }
        agy_lock_path = self.antigravity / "dependency-lock.json"
        agy_lock_path.write_text(json.dumps(agy_lock, indent=2) + "\n", encoding="utf-8")
        (self.antigravity / "pyproject.toml").write_bytes(b"[project]\nname='agy'\n")
        package_root = self.antigravity / "src" / "antigravity_adapter"
        package_root.mkdir(parents=True, exist_ok=True)
        for name in ANTIGRAVITY_FIXTURE_FILES:
            (package_root / name).write_text(f"# {name}\n", encoding="utf-8")
        agy_source_paths = [
            *(package_root / name for name in ANTIGRAVITY_FIXTURE_FILES),
            agy_lock_path,
            self.antigravity / "pyproject.toml",
        ]
        agy_source_sha = promotion._source_inventory_sha256(
            self.antigravity.resolve(), agy_source_paths
        )
        model_contract_sha = hashlib.sha256(
            json.dumps(
                agy_lock["agy"]["model_efforts"],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        (self.antigravity / "runtime-identity.json").write_text(
            json.dumps(
                {
                    "schema_version": "antigravity-adapter-runtime-identity-v3",
                    "component": "antigravity-adapter",
                    "runtime_version": "2.1.0",
                    "release_id": "antigravity-adapter-test",
                    "route_schema_version": "3.0",
                    "route_registry_schema_version": 3,
                    "authority_pointer_schema_version": "capability-authority-pointer-v1",
                    "supported_manifest_schema_versions": ["1.2", "1.3"],
                    "agy_version": "1.1.13",
                    "agy_executable_sha256": sha256(self.agy),
                    "agy_model_contract_sha256": model_contract_sha,
                    "dependency_lock_schema_version": "antigravity-adapter-dependency-lock-v2",
                    "dependency_lock_sha256": sha256(agy_lock_path),
                    "python_execution_closure": self.python_closures[
                        "antigravity-adapter"
                    ],
                    "source_sha256": agy_source_sha,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self._install_gateway_identity()
        self._write_config()

    def _install_python_closure(
        self, root: Path, command: Path, package: str
    ) -> dict[str, object]:
        pyvenv = root / ".venv" / "pyvenv.cfg"
        pyvenv.write_text(
            "\n".join(
                (
                    f"home = {self.base_python_home}",
                    "implementation = CPython",
                    "version_info = 3.11",
                    "include-system-site-packages = false",
                    "",
                )
            ),
            encoding="utf-8",
        )
        site_packages = root / ".venv" / "Lib" / "site-packages"
        site_packages.mkdir(parents=True)
        editable = site_packages / f"__editable__.{package}-test.pth"
        source_root = (root / "src").resolve()
        editable.write_text(str(source_root) + "\n", encoding="utf-8")
        virtualenv_pth = site_packages / "_virtualenv.pth"
        virtualenv_module = site_packages / "_virtualenv.py"
        installed_module = site_packages / "fixture_dependency.py"
        virtualenv_pth.write_text("import _virtualenv\n", encoding="utf-8")
        virtualenv_module.write_text("# fixture bootstrap\n", encoding="utf-8")
        installed_module.write_text("VALUE = 'trusted'\n", encoding="utf-8")
        dist_info = site_packages / f"fixture_{package}-1.0.0.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(
            f"Name: fixture-{package}\nVersion: 1.0.0\n",
            encoding="utf-8",
        )
        (dist_info / "RECORD").write_text(
            "\n".join(
                (
                    f"{editable.name},,",
                    f"{installed_module.name},,",
                    f"{dist_info.name}/METADATA,,",
                    f"{dist_info.name}/RECORD,,",
                    "",
                )
            ),
            encoding="utf-8",
        )
        spec = promotion.WORKER_SERVER_SPECS[
            "local-agent-stack"
            if package == "local_agent_stack"
            else "antigravity-adapter"
        ]
        pycache_prefix = root.joinpath(
            *str(spec["pycache_relative_path"]).split("/")
        )
        pycache_prefix.mkdir(parents=True)
        origin = source_root / package / "__init__.py"
        with mock.patch.object(
            promotion,
            "_probe_pth_import_origins",
            return_value={"_virtualenv": str(virtualenv_module.resolve())},
        ):
            distributions = promotion._installed_distributions_identity(
                site_packages,
                root / ".venv",
                source_root,
                command,
                pycache_prefix,
            )
        base_runtime = promotion._worker_base_runtime_tree_identity(
            self.base_python_home
        )
        site_packages_tree = promotion._worker_site_packages_tree_identity(
            site_packages
        )
        return {
            "schema_version": promotion.PYTHON_EXECUTION_CLOSURE_SCHEMA,
            "venv_python_path": str(command.resolve()),
            "venv_python_sha256": sha256(command),
            "pyvenv_config_path": str(pyvenv.resolve()),
            "pyvenv_config_sha256": sha256(pyvenv),
            "include_system_site_packages": False,
            "base_interpreter_path": str(self.base_python.resolve()),
            "base_interpreter_version": "3.11.15",
            "base_interpreter_sha256": sha256(self.base_python),
            "base_runtime_tree_path": str(self.base_python_home.resolve()),
            **base_runtime,
            "site_packages_path": str(site_packages.resolve()),
            **site_packages_tree,
            **distributions,
            "editable_pth_path": str(editable.resolve()),
            "editable_pth_sha256": sha256(editable),
            "editable_source_root": str(source_root),
            "import_package": package,
            "import_origin": str(origin.resolve(strict=False)),
            "isolated_mode": True,
            "user_site_enabled": False,
            "dont_write_bytecode": True,
            "pycache_prefix_path": str(pycache_prefix.resolve()),
            "pycache_prefix_empty": True,
            "forbidden_environment_variables": list(
                promotion.PYTHON_FORBIDDEN_ENVIRONMENT_VARIABLES
            ),
            "child_environment_policy_id": (
                promotion.WORKER_CHILD_ENVIRONMENT_POLICY_ID
            ),
        }

    def _python_probe(
        self, command: Path, package: str, pycache_prefix: Path
    ) -> dict[str, object]:
        root = self.las if package == "local_agent_stack" else self.antigravity
        return {
            "executable": str(command.resolve()),
            "base_prefix": str(self.base_python_home.resolve()),
            "version": "3.11.15",
            "origin": str((root / "src" / package / "__init__.py").resolve()),
            "locations": [str((root / "src" / package).resolve())],
            "isolated": 1,
            "no_user_site": 1,
            "user_site_enabled": False,
            "dont_write_bytecode": True,
            "pycache_prefix": str(pycache_prefix.resolve()),
        }

    def _pth_probe(
        self, command: Path, modules: list[str], pycache_prefix: Path
    ) -> dict[str, str]:
        root = self.las if command == self.las_command else self.antigravity
        site_packages = root / ".venv" / "Lib" / "site-packages"
        return {
            module: str((site_packages / f"{module}.py").resolve())
            for module in modules
        }

    def _install_gateway_identity(self) -> None:
        gateway_root = self.root / "tools" / "codex-stability"
        gateway_root.mkdir(parents=True)
        for name in promotion.GATEWAY_SOURCE_RELATIVE_PATHS:
            gateway_root.joinpath(*name.split("/")).write_text(
                f"# {name}\n", encoding="utf-8"
            )
        gateway_site = gateway_root / ".venv" / "Lib" / "site-packages"
        gateway_site.mkdir(parents=True)
        (gateway_site / "gateway_dependency.py").write_text(
            "# dependency\n", encoding="utf-8"
        )
        gateway_lock = gateway_root / "uv.lock"
        gateway_lock.write_text("fixture lock\n", encoding="utf-8")
        gateway_pycache = (
            self.local_app_data / "Codex" / "stability" / "pycache" / "gateway"
        )
        gateway_pycache.mkdir(parents=True)
        source_sha, source_files = promotion._gateway_inventory_digest(
            gateway_root,
            promotion.GATEWAY_SOURCE_RELATIVE_PATHS,
            domain=promotion.GATEWAY_SOURCE_DOMAIN,
        )
        base_identity = promotion._gateway_runtime_tree_identity(
            self.base_python_home,
            domain=promotion.GATEWAY_PYTHON_BASE_RUNTIME_DOMAIN,
        )
        site_identity = promotion._gateway_runtime_tree_identity(
            gateway_site,
            domain=promotion.GATEWAY_SITE_PACKAGES_DOMAIN,
        )
        identity = {
            "child_environment_policy_id": (
                promotion.WORKER_CHILD_ENVIRONMENT_POLICY_ID
            ),
            "component": promotion.GATEWAY_COMPONENT,
            "gateway_startup_environment_policy_id": (
                promotion.GATEWAY_STARTUP_ENVIRONMENT_POLICY_ID
            ),
            "gateway_startup_python_flags": dict(
                promotion.GATEWAY_REQUIRED_PYTHON_FLAGS
            ),
            "python_bytecode_cache": {
                "must_be_empty": True,
                "prefix_path": str(gateway_pycache.resolve()),
            },
            "python_injection_environment_keys": list(
                promotion.PYTHON_FORBIDDEN_ENVIRONMENT_VARIABLES
            ),
            "python_runtime": {
                "base_root": str(self.base_python_home.resolve()),
                "base_runtime_file_count": base_identity["file_count"],
                "base_runtime_sha256": base_identity["sha256"],
                "console_executable_path": str(self.base_python.resolve()),
                "console_executable_sha256": sha256(self.base_python),
                "dependency_lock_path": str(gateway_lock.resolve()),
                "dependency_lock_sha256": sha256(gateway_lock),
                "site_packages_file_count": site_identity["file_count"],
                "site_packages_path": str(gateway_site.resolve()),
                "site_packages_sha256": site_identity["sha256"],
                "version": "3.11.15",
                "windowless_executable_path": str(
                    (self.base_python_home / "pythonw.exe").resolve()
                ),
                "windowless_executable_sha256": sha256(
                    self.base_python_home / "pythonw.exe"
                ),
            },
            "release_id": promotion.GATEWAY_RELEASE_ID,
            "schema_version": promotion.GATEWAY_RUNTIME_IDENTITY_SCHEMA,
            "source_files": source_files,
            "source_sha256": source_sha,
        }
        (gateway_root / "runtime-identity.json").write_text(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def _write_config(self) -> None:
        las_cache = self.python_closures["local-agent-stack"][
            "pycache_prefix_path"
        ]
        antigravity_cache = self.python_closures["antigravity-adapter"][
            "pycache_prefix_path"
        ]
        self.config.write_text(
            "\n".join(
                (
                    "[mcp_servers.local-agent-stack]",
                    "enabled = false",
                    "gateway_managed = true",
                    f"command = '{self.las_command}'",
                    f"args = ['-I', '-B', '-X', 'pycache_prefix={las_cache}', '-m', 'local_agent_stack.server']",
                    f"cwd = '{self.las}'",
                    "startup_timeout_sec = 60.0",
                    "tool_timeout_sec = 660.0",
                    f"env = {{ LOCAL_AGENT_STACK_ROOT = '{self.las}' }}",
                    "",
                    "[mcp_servers.antigravity-adapter]",
                    "enabled = false",
                    "gateway_managed = true",
                    f"command = '{self.antigravity_command}'",
                    f"args = ['-I', '-B', '-X', 'pycache_prefix={antigravity_cache}', '-m', 'antigravity_adapter.server']",
                    f"cwd = '{self.antigravity}'",
                    "startup_timeout_sec = 30.0",
                    "tool_timeout_sec = 620.0",
                    f"env = {{ ANTIGRAVITY_ADAPTER_ROOT = '{self.antigravity}', ANTIGRAVITY_AGY_EXECUTABLE = '{self.agy}' }}",
                    "",
                    "[mcp_servers.codex-stability-gateway]",
                    "url = 'http://127.0.0.1:8765/mcp'",
                    "",
                )
            ),
            encoding="utf-8",
        )

    def render(self) -> dict:
        result = promotion.render_bom(
            self.config,
            SCHEMA,
            [
                "local-agent-stack=runtime-identity.json",
                "antigravity-adapter=runtime-identity.json",
            ],
        )
        self.candidate.write_bytes(result["bom_bytes"])
        return result

    def options(self, rendered: dict, *, transaction_id: str = "bom-test-1", **kwargs):
        return promotion.ApplyOptions(
            candidate_path=self.candidate,
            schema_path=SCHEMA,
            config_path=self.config,
            target_path=self.target,
            transaction_id=transaction_id,
            expected_target_sha256=kwargs.get("expected_target_sha256", promotion.MISSING),
            expected_candidate_sha256=rendered["bom_sha256"],
            fault_injection=kwargs.get("fault_injection"),
        )

    def test_render_binds_nested_and_root_compatibility_identities(self) -> None:
        rendered = self.render()
        runtimes = rendered["bom"]["runtimes"]
        self.assertEqual(
            runtimes["local-agent-stack"]["release_id"], "local-agent-stack-test"
        )
        self.assertEqual(
            runtimes["antigravity-adapter"]["release_id"],
            "antigravity-adapter-test",
        )
        self.assertEqual(rendered["bom_sha256"], sha256(self.candidate))

    def test_apply_is_compare_and_swap_and_replays_exact_receipt(self) -> None:
        rendered = self.render()
        first = promotion.apply_bom(self.options(rendered))
        second = promotion.apply_bom(self.options(rendered))
        self.assertEqual(first, second)
        self.assertEqual(first["outcome"], "completed")
        self.assertEqual(first["promoter_sha256"], sha256(MODULE_PATH))
        journal = json.loads(
            (
                self.target.parent
                / promotion.STATE_DIRECTORY
                / "transactions"
                / "bom-test-1"
                / "journal.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(journal["promoter_sha256"], sha256(MODULE_PATH))
        self.assertEqual(sha256(self.target), rendered["bom_sha256"])
        with self.assertRaises(promotion.PreconditionError):
            promotion.apply_bom(
                self.options(rendered, transaction_id="bom-test-stale")
            )

    def test_promoter_drift_between_preflight_and_mutex_is_rejected(self) -> None:
        rendered = self.render()
        with mock.patch.object(
            promotion,
            "_promoter_sha256",
            side_effect=["1" * 64, "2" * 64],
        ):
            with self.assertRaisesRegex(
                promotion.PreconditionError,
                "inputs changed after lock",
            ):
                promotion.apply_bom(
                    self.options(rendered, transaction_id="promoter-drift")
                )
        self.assertFalse(self.target.exists())

    def test_identity_drift_after_render_is_rejected_before_promotion(self) -> None:
        rendered = self.render()
        identity = self.las / "runtime-identity.json"
        value = json.loads(identity.read_text(encoding="utf-8"))
        value["release_id"] = "local-agent-stack-drifted"
        identity.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(promotion.BomValidationError):
            promotion.apply_bom(self.options(rendered))
        self.assertFalse(self.target.exists())

    def test_worker_omission_and_addition_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            promotion.BomValidationError, "exact LAS and Antigravity worker closure"
        ):
            promotion.render_bom(
                self.config,
                SCHEMA,
                ["local-agent-stack=runtime-identity.json"],
            )
        with self.assertRaisesRegex(
            promotion.BomValidationError, "exact LAS and Antigravity worker closure"
        ):
            promotion.render_bom(
                self.config,
                SCHEMA,
                [
                    "local-agent-stack=runtime-identity.json",
                    "antigravity-adapter=runtime-identity.json",
                    "unexpected=runtime-identity.json",
                ],
            )

    def test_unrelated_config_change_does_not_invalidate_reviewed_candidate(self) -> None:
        rendered = self.render()
        self.config.write_text(
            self.config.read_text(encoding="utf-8")
            + "\n[unrelated]\npresentation = 'changed'\n",
            encoding="utf-8",
        )
        receipt = promotion.apply_bom(self.options(rendered))
        self.assertEqual(receipt["outcome"], "completed")

    def test_execution_defining_worker_config_mutations_are_rejected(self) -> None:
        substitutions = {
            "enabled": ("enabled = false", "enabled = true"),
            "args": ("local_agent_stack.server", "other.server"),
            "env": ("LOCAL_AGENT_STACK_ROOT", "UNBOUNDED_ROOT"),
            "startup_timeout": ("startup_timeout_sec = 60.0", "startup_timeout_sec = 61.0"),
            "tool_timeout": ("tool_timeout_sec = 660.0", "tool_timeout_sec = 661.0"),
            "configured_policy_injection": (
                f"env = {{ LOCAL_AGENT_STACK_ROOT = '{self.las}' }}",
                f"env = {{ LOCAL_AGENT_STACK_ROOT = '{self.las}', "
                "CODEX_STABILITY_CHILD_ENV_POLICY_ID = "
                "'codex-stability-child-env-v1' }",
            ),
        }
        for label, (old, new) in substitutions.items():
            with self.subTest(label=label):
                self._write_config()
                rendered = self.render()
                self.config.write_text(
                    self.config.read_text(encoding="utf-8").replace(old, new, 1),
                    encoding="utf-8",
                )
                with self.assertRaises(promotion.BomValidationError):
                    promotion.apply_bom(
                        self.options(rendered, transaction_id=f"mutation-{label}")
                    )

    def test_python_execution_chain_mutations_are_rejected(self) -> None:
        paths = {
            "pyvenv": self.las / ".venv" / "pyvenv.cfg",
            "base_interpreter": self.base_python,
            "editable_pth": Path(
                self.python_closures["local-agent-stack"]["editable_pth_path"]
            ),
        }
        for label, path in paths.items():
            with self.subTest(label=label):
                original = path.read_bytes()
                rendered = self.render()
                path.write_bytes(original + b"tampered")
                try:
                    with self.assertRaises(promotion.BomValidationError):
                        promotion.apply_bom(
                            self.options(
                                rendered,
                                transaction_id=f"python-chain-{label}",
                            )
                        )
                finally:
                    path.write_bytes(original)

    def test_site_packages_byte_mutation_and_unowned_shadow_are_rejected(self) -> None:
        installed = (
            self.las / ".venv" / "Lib" / "site-packages" / "fixture_dependency.py"
        )
        original = installed.read_bytes()
        original_stat = installed.stat()
        rendered = self.render()
        installed.write_bytes(original.replace(b"trusted", b"hostile"))
        os.utime(
            installed,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        with self.assertRaisesRegex(
            promotion.BomValidationError,
            "dependency closure does not match current bytes",
        ):
            promotion.apply_bom(
                self.options(
                    rendered,
                    transaction_id="site-packages-byte-mutation",
                )
            )
        installed.write_bytes(original)

        for suffix, payload in (
            (".py", b"VALUE = 'shadow'\n"),
            (".pyc", b"sourceless-shadow-bytecode"),
        ):
            with self.subTest(suffix=suffix):
                rendered = self.render()
                shadow = installed.parent / f"unowned_shadow{suffix}"
                shadow.write_bytes(payload)
                try:
                    with self.assertRaisesRegex(
                        promotion.BomValidationError,
                        "dependency closure does not match current bytes",
                    ):
                        promotion.apply_bom(
                            self.options(
                                rendered,
                                transaction_id=(
                                    "site-packages-shadow-addition-"
                                    + suffix.removeprefix(".")
                                ),
                            )
                        )
                finally:
                    shadow.unlink()

    def test_added_antigravity_python_module_is_in_source_identity(self) -> None:
        added = self.antigravity / "src" / "antigravity_adapter" / "injected.py"
        added.write_text("# newly imported bytes\n", encoding="utf-8")
        with self.assertRaisesRegex(
            promotion.BomValidationError,
            "source identity does not match bytes",
        ):
            self.render()

    def test_cross_family_and_hollow_identities_are_rejected(self) -> None:
        las_identity = self.las / "runtime-identity.json"
        antigravity_identity = self.antigravity / "runtime-identity.json"
        original_las = las_identity.read_bytes()
        original_antigravity = antigravity_identity.read_bytes()
        las_identity.write_bytes(original_antigravity)
        with self.assertRaises(promotion.BomValidationError):
            self.render()
        antigravity_identity.write_bytes(original_antigravity)
        fake = json.loads(original_las)
        fake["source_sha256"] = "f" * 64
        las_identity.write_text(json.dumps(fake, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(
            promotion.BomValidationError, "source identity does not match bytes"
        ):
            self.render()
        las_identity.write_bytes(original_las)
        hollow = json.loads(original_antigravity)
        hollow.pop("dependency_lock_sha256")
        antigravity_identity.write_text(
            json.dumps(hollow, indent=2) + "\n", encoding="utf-8"
        )
        with self.assertRaises(promotion.BomValidationError):
            self.render()

    def test_post_promotion_failure_rolls_back_exact_baseline(self) -> None:
        baseline = b'{"old":true}\n'
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(baseline)
        rendered = self.render()
        with self.assertRaises(promotion.InjectedFailure):
            promotion.apply_bom(
                self.options(
                    rendered,
                    expected_target_sha256=hashlib.sha256(baseline).hexdigest(),
                    fault_injection="after_promote",
                )
            )
        self.assertEqual(self.target.read_bytes(), baseline)

    def test_schema_invalid_candidate_is_rejected(self) -> None:
        rendered = self.render()
        value = json.loads(self.candidate.read_text(encoding="utf-8"))
        value["unexpected"] = True
        self.candidate.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(promotion.BomValidationError, "schema-invalid"):
            promotion.apply_bom(
                promotion.ApplyOptions(
                    candidate_path=self.candidate,
                    schema_path=SCHEMA,
                    config_path=self.config,
                    target_path=self.target,
                    transaction_id="bom-test-invalid",
                    expected_target_sha256=promotion.MISSING,
                    expected_candidate_sha256=sha256(self.candidate),
                )
            )


if __name__ == "__main__":
    unittest.main()
