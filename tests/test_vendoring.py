"""Regressions for the files actually deployed by HACS and their reproducible source."""
import ast
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

from packaging.requirements import Requirement
import pytest

from tools.vendor_library import TARGET, snapshot, synchronize, verify

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/gac_connect"


def test_bundle_matches_its_recorded_hashes_and_identity():
    from custom_components.gac_connect._vendor import LIBRARY_COMMIT, LIBRARY_SOURCE, LIBRARY_VERSION
    from custom_components.gac_connect._vendor import gac_connect

    provenance = verify(TARGET)
    assert (LIBRARY_VERSION, LIBRARY_COMMIT, LIBRARY_SOURCE) == (
        provenance["version"], provenance["commit"], provenance["source"],
    )
    assert gac_connect.__version__ == LIBRARY_VERSION
    assert len(LIBRARY_COMMIT) == 40 and int(LIBRARY_COMMIT, 16)
    assert "cli.py" in provenance["excluded"]
    assert not (TARGET / "gac_connect/cli.py").exists()
    assert "MIT License" in (TARGET / "gac_connect/LICENSE").read_text()


def test_manifest_and_installed_dependencies_support_the_bundle():
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    declared = {Requirement(item).name: Requirement(item) for item in manifest["requirements"]}
    ha_requirements = {Requirement(item).name for item in importlib.metadata.requires("homeassistant")}
    assert "gac-connect" not in declared
    dev_requirements = (ROOT / "requirements-dev.txt").read_text().splitlines()
    for item in manifest["requirements"]:
        assert item in dev_requirements
        requirement = Requirement(item)
        assert importlib.metadata.version(requirement.name) in requirement.specifier
    for item in verify(TARGET)["dependencies"]:
        requirement = Requirement(item)
        assert requirement.name in declared or requirement.name in ha_requirements
        assert importlib.metadata.version(requirement.name) in requirement.specifier


def test_no_integration_module_imports_an_external_library():
    for path in COMPONENT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] != "gac_connect" for alias in node.names), path
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                assert (node.module or "").split(".")[0] != "gac_connect", path


@pytest.mark.parametrize("external", ["absent", "incompatible"])
def test_deployed_component_works_without_checkout_or_external_library(tmp_path, external):
    """Import only a HACS-shaped deployment in a fresh interpreter, then exercise its keys."""
    deployment = tmp_path / "config"
    shutil.copytree(COMPONENT, deployment / "custom_components/gac_connect",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    if external == "incompatible":
        package = deployment / "gac_connect"
        package.mkdir()
        (package / "__init__.py").write_text('marker = object()\n__version__ = "incompatible"\n')
    script = '''
import importlib
import importlib.abc
from importlib.resources import files
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
if sys.argv[2] == "incompatible":
    import gac_connect as external
    marker = external.marker
else:
    class NoExternalLibrary(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "gac_connect" or fullname.startswith("gac_connect."):
                raise ModuleNotFoundError("External gac_connect is not installed")
    sys.meta_path.insert(0, NoExternalLibrary())

from custom_components.gac_connect import api, helpers
from custom_components.gac_connect.const import PLATFORMS
from custom_components.gac_connect._vendor import gac_connect as library
from custom_components.gac_connect._vendor.gac_connect.crypto import encrypt_envelope
from custom_components.gac_connect._vendor.gac_connect.commands import CATALOG, build_body
from custom_components.gac_connect._vendor.gac_connect.keys import load_material
from custom_components.gac_connect._vendor.gac_connect.limits import DEFAULT_LIMITER
from custom_components.gac_connect._vendor.gac_connect.models import VehicleStatus

for platform in [*PLATFORMS, "config_flow", "diagnostics", "services"]:
    importlib.import_module(f"custom_components.gac_connect.{platform}")
assert Path(library.__file__).is_relative_to(Path(sys.argv[1]))
assert helpers.DEFAULT_LIMITER is DEFAULT_LIMITER
assert api.GacClient is library.GacClient
assert isinstance(api.decode_status({}), VehicleStatus)
assert api.decode_status({'powerAccessors': [{'powerMode': 2}]}).power_on is True
assert api.decode_status({'powerAccessors': [{'powerMode': 0}]}).power_on is False
assert api.decode_status({'powerAccessors': [{'powerMode': 1}]}).power_on is None
assert CATALOG['vehicle-power-off'].path.endswith('/power/off')
assert build_body(CATALOG['vehicle-power-off'], 'VIN') == {'vin': 'VIN'}
assert files(library).joinpath("py.typed").is_file()
for region, bundle in [("AU", "_material.pem"), ("GB", "_material_uk.pem")]:
    material = load_material(bundle)
    message = {"success": True, "data": {"synthetic": 1}}
    envelope, _, _ = encrypt_envelope(message, material.iov_response.public)
    client = api.GacClient(region, object(), material=material)
    assert client.decrypt_push(envelope) == message
if sys.argv[2] == "incompatible":
    assert sys.modules["gac_connect"] is external and external.marker is marker
    assert external.__version__ == "incompatible"
else:
    assert "gac_connect" not in sys.modules
assert not any("._compat_b1" in name for name in sys.modules)
print("Standalone deployed component, private imports and both material bundles passed")
'''
    result = subprocess.run([sys.executable, "-I", "-c", script, str(deployment), external],
                            cwd=tmp_path, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def source_repo(tmp_path):
    """An offline Git source with the same layout; never commit to the real repositories."""
    source = tmp_path / "source"
    package = source / "src/gac_connect"
    package.mkdir(parents=True)
    for name, text in {"__init__.py": '__version__ = "1.0"\n', "keys.py": "# original\n",
                       "_material.pem": "synthetic", "py.typed": "", "cli.py": "# optional CLI"}.items():
        (package / name).write_text(text)
    (source / "LICENSE").write_text("MIT License\nSynthetic test fixture\n")
    (source / "pyproject.toml").write_text(
        '[project]\nversion = "1.0"\nrequires-python = ">=3.11"\ndependencies = ["aiohttp>=3.9"]\n'
    )
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    commit_fixture(source)
    return source


def commit_fixture(source):
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Test fixture"],
                   check=True)


def test_sync_is_exact_repeatable_and_uses_committed_source(source_repo, tmp_path):
    target = tmp_path / "bundle"
    synchronize(source_repo, target, check=False)
    original = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    synchronize(source_repo, target, check=False)
    assert original == {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert not (target / "gac_connect/cli.py").exists()
    assert (target / "gac_connect/LICENSE").read_bytes() == (source_repo / "LICENSE").read_bytes()
    # Uncommitted work is preserved and cannot be falsely attributed to the old commit.
    keys = source_repo / "src/gac_connect/keys.py"
    keys.write_text("# uncommitted change\n")
    synchronize(source_repo, target, check=False)
    assert (target / "gac_connect/keys.py").read_text() == "# original\n"
    assert keys.read_text() == "# uncommitted change\n"
    commit_fixture(source_repo)
    synchronize(source_repo, target, check=True)  # Still compares the recorded commit, not new HEAD.
    with pytest.raises(ValueError, match="differs from the selected source"):
        synchronize(source_repo, target, check=True, ref="HEAD")
    synchronize(source_repo, target, check=False)
    assert (target / "gac_connect/keys.py").read_text() == "# uncommitted change\n"
    synchronize(source_repo, target, check=True)


@pytest.mark.parametrize("damage", ["modified", "missing", "unexpected", "symlink"])
def test_sync_detects_drift_and_preserves_unreviewed_files(source_repo, tmp_path, damage):
    target = tmp_path / "bundle"
    synchronize(source_repo, target, check=False)
    keys = target / "gac_connect/keys.py"
    if damage == "modified":
        keys.write_text("# local edit\n")
    elif damage == "missing":
        keys.unlink()
    elif damage == "unexpected":
        keys = target / "notes.txt"
        keys.write_text("keep my notes")
    else:
        keys.unlink()
        keys.symlink_to(source_repo / "src/gac_connect/keys.py")
    before = keys.read_bytes() if keys.exists() else None
    for check in (True, False):
        with pytest.raises(ValueError, match="differ|symlink"):
            synchronize(source_repo, target, check=check)
    assert (keys.read_bytes() if keys.exists() else None) == before


def test_sync_removes_only_obsolete_generated_source(source_repo, tmp_path):
    target = tmp_path / "bundle"
    synchronize(source_repo, target, check=False)
    (source_repo / "src/gac_connect/keys.py").unlink()
    commit_fixture(source_repo)
    synchronize(source_repo, target, check=False)
    assert not (target / "gac_connect/keys.py").exists()
    assert verify(target)["sha256"]
    assert snapshot(source_repo, "HEAD")["gac_connect/_material.pem"] == b"synthetic"


def test_finder_metadata_does_not_invalidate_a_manually_copied_bundle(source_repo, tmp_path):
    target = tmp_path / "bundle"
    synchronize(source_repo, target, check=False)
    metadata = target / ".DS_Store"
    metadata.write_bytes(b"Finder metadata")
    synchronize(source_repo, target, check=True)
    assert metadata.read_bytes() == b"Finder metadata"
