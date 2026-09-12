from pathlib import Path

import yaml
from fsm import DESTRUCTIVE_ENABLED, VERSION

ROOT = Path(__file__).parents[1]


def test_manifest_supported_mappings_ingress_and_permissions():
    config = yaml.safe_load((ROOT / "frigate_storage_manager/config.yaml").read_text())
    assert config["version"] == VERSION == "0.1.4"
    assert config["arch"] == ["amd64"]
    assert config["ingress"] and config["panel_admin"]
    assert config["hassio_role"] == "manager" and config["hassio_api"]
    assert {x["type"] for x in config["map"]} == {"media", "all_addon_configs"}
    assert not any(
        config.get(k) for k in ("host_network", "docker_api", "full_access", "privileged", "ports")
    )
    assert not DESTRUCTIVE_ENABLED
    assert not any("enable" in k or "destructive" in k for k in config["options"])
    repo = yaml.safe_load((ROOT / "repository.yaml").read_text())
    assert repo["url"] == "https://github.com/PacoCotera/frigate-storage-manager"


def test_dockerfile_has_explicit_base_startup_and_no_unused_build_yaml():
    dockerfile = (ROOT / "frigate_storage_manager/Dockerfile").read_text()
    assert "FROM python:3.12.12-slim-bookworm" in dockerfile
    assert 'CMD ["python", "-m", "fsm"]' in dockerfile
    assert not (ROOT / "frigate_storage_manager/build.yaml").exists()
    assert (ROOT / "LICENSE").read_text().startswith("MIT License")
