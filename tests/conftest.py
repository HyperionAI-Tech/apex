import pytest

from apex.lean.project import LeanProject, LeanUnavailable


@pytest.fixture(scope="session")
def lean_project() -> LeanProject:
    try:
        project = LeanProject.locate()
        project.lean_command()
    except LeanUnavailable as e:
        pytest.skip(f"Lean unavailable: {e}")
    if not project.prelude_built:
        pytest.skip("Apex prelude not built; run `apex init`")
    return project
