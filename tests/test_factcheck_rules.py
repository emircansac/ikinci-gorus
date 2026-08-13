import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "factcheck",
    Path(__file__).parent.parent / "pipeline" / "03_factcheck.py",
)
factcheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factcheck)


def test_drug_interaction_claim_detected():
    text = "Lahana ve marulda bulunan yüksek K vitamini, kan sulandırıcı antikoagülan ilaçlarla doğrudan etkileşime girer."
    assert factcheck.is_drug_interaction_claim(text)


def test_non_drug_claim_not_flagged():
    assert not factcheck.is_drug_interaction_claim("Ispanak potasyum içerir")
