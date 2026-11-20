"""Host checks for the static H4 task tasks/concrete-fracture-size-effect (no solver needed)."""
import ast
import json
import pathlib
import tomllib

TASK = pathlib.Path(__file__).resolve().parents[1] / "tasks" / "concrete-fracture-size-effect"


def test_layout():
    for f in ["instruction.md", "task.toml", "README.md", "environment/Dockerfile", "environment/prepare_data.py",
              "environment/data_README.md", "tests/Dockerfile", "tests/test.sh", "tests/harness_run.py",
              "tests/test_outputs.py", "tests/prepare_data.py", "solution/solve.sh", "solution/model.py",
              "solution/tpb_oofem.py", "solution/parameters.json", "solution/config.json"]:
        assert (TASK / f).is_file(), f


def test_data_builder_identical_in_both_images():
    a = (TASK / "environment/prepare_data.py").read_bytes()
    b = (TASK / "tests/prepare_data.py").read_bytes()
    assert a == b


def test_python_sources_parse():
    for f in TASK.rglob("*.py"):
        ast.parse(f.read_text(encoding="utf-8"), filename=str(f))


def test_task_toml_and_solution_config():
    cfg = tomllib.loads((TASK / "task.toml").read_text(encoding="utf-8"))
    assert cfg["agent"]["timeout_sec"] == 28800.0
    assert cfg["verifier"]["environment"]["network_mode"] == "no-network"
    assert cfg["artifacts"] == ["/app/results"]
    h = json.loads((TASK / "solution/config.json").read_text())["h_mm"]
    assert set(h) == {"40", "92.8", "215", "500"}


def test_no_dataset_records_in_repo():
    # the database is downloaded at image build time and must not be committed
    assert not (TASK / "environment/data").exists()
    assert not (TASK / "tests/reference.json").exists()
