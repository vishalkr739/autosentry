from pathlib import Path

import pytest

from autosentry_agent.ingestion.corpus import CorpusError, load_corpus

VALID = """
graph = "AutosentryRegulatoryKB"

[access]
tenant_id = "global"
roles = ["analyst"]

[[documents]]
doc_id = "fatf_recommendations"
title = "FATF Recommendations"
url = "https://example.org/docs/recs.pdf"
sha256 = "ABC123"
doc_type = "standard"
source = "FATF"
classification = "PUBLIC"

[[documents]]
doc_id = "fincen_advisory"
title = "FinCEN advisory"
url = "https://example.org/download?id=7"
file_type = "pdf"
doc_type = "advisory"
source = "FinCEN"
jurisdiction = "US"

[[questions]]
question = "What are the FATF red flags?"
expected_doc_id = "fatf_recommendations"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "corpus.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_documents_questions_and_access(tmp_path):
    corpus = load_corpus(write(tmp_path, VALID))

    assert corpus.graph == "AutosentryRegulatoryKB"
    assert corpus.access.roles == frozenset({"analyst"})
    recs, advisory = corpus.documents
    assert recs.suffix == ".pdf"
    assert recs.sha256 == "abc123"
    assert recs.metadata.classification == "PUBLIC"
    assert advisory.suffix == ".pdf"
    assert advisory.sha256 == ""
    assert corpus.questions[0].expected_doc_id == "fatf_recommendations"


def test_classification_defaults_to_restricted(tmp_path):
    corpus = load_corpus(write(tmp_path, VALID))
    assert corpus.documents[1].metadata.classification == "RESTRICTED"
    assert corpus.documents[1].metadata.allowed_roles == frozenset()


@pytest.mark.parametrize("bad_id", ["FATF_Recs", "fatf recs", "fatf/recs", "fatf-(1)"])
def test_rejects_doc_ids_graphrag_would_rewrite(tmp_path, bad_id):
    with pytest.raises(CorpusError, match="must match"):
        load_corpus(write(tmp_path, VALID.replace("fatf_recommendations", bad_id, 1)))


def test_rejects_unsupported_file_type(tmp_path):
    with pytest.raises(CorpusError, match="file type"):
        load_corpus(write(tmp_path, VALID.replace('file_type = "pdf"', 'file_type = "exe"')))


def test_rejects_url_without_file_type(tmp_path):
    with pytest.raises(CorpusError, match="none"):
        load_corpus(write(tmp_path, VALID.replace('file_type = "pdf"\n', "")))


def test_rejects_duplicate_doc_ids(tmp_path):
    with pytest.raises(CorpusError, match="duplicate"):
        load_corpus(write(tmp_path, VALID.replace('"fincen_advisory"', '"fatf_recommendations"')))


def test_rejects_question_for_unknown_document(tmp_path):
    with pytest.raises(CorpusError, match="unknown doc_id"):
        load_corpus(write(tmp_path, VALID.replace('expected_doc_id = "fatf_recommendations"', 'expected_doc_id = "nope"')))


def test_reports_missing_required_key(tmp_path):
    with pytest.raises(CorpusError, match="source"):
        load_corpus(write(tmp_path, VALID.replace('source = "FinCEN"\n', "")))
