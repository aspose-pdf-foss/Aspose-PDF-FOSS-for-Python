import pytest
from aspose_pdf.security import SignaturesCompromiseDetector, CompromiseCheckResult


class DummySignature:
    def __init__(self, valid: bool = True):
        self.valid = valid


class DummyDocument:
    def __init__(self, signatures=None):
        self.signatures = signatures or []


@pytest.mark.parametrize(
    "doc,expected_compromised,expected_reason",
    [
        (DummyDocument(), False, "unsigned document"),
        (DummyDocument(signatures=[DummySignature(valid=True)]), False, None),
        (
            DummyDocument(signatures=[DummySignature(valid=False)]),
            True,
            "corrupted signature",
        ),
    ],
)
def test_check_returns_result(doc, expected_compromised, expected_reason):
    detector = SignaturesCompromiseDetector(document=doc)
    result = detector.check()
    assert isinstance(result, CompromiseCheckResult)
    assert result.compromised == expected_compromised
    if expected_reason:
        assert expected_reason in result.reasons
    else:
        assert not result.reasons
