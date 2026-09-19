from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def ldf_path() -> Path:
    return FIXTURES / "25 - Tipo.LDF"


@pytest.fixture(scope="session")
def lst_path() -> Path:
    return FIXTURES / "25 - Tipo.LST"


@pytest.fixture(scope="session")
def ldf_doc(ldf_path):
    from tqs2etabs.importers.tqs.ldf import parse_ldf
    return parse_ldf(ldf_path)


@pytest.fixture(scope="session")
def lst_doc(lst_path):
    from tqs2etabs.importers.tqs.lst import parse_lst
    return parse_lst(lst_path)


@pytest.fixture(scope="session")
def model(ldf_doc, lst_doc):
    from tqs2etabs.importers.tqs.ldf import build_model
    return build_model(ldf_doc, lst_doc)
