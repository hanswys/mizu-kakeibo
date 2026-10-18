from __future__ import annotations

import copy
import json
from itertools import count

import httpx
import pytest
from jobs_dataset import dataset, envelope, example, other, raw_message
from pydantic import ValidationError

from mizu_kakeibo import cli
from mizu_kakeibo.jobs.constants import JOB_ANALYSIS_OUTPUT_CONTRACT, JOBS_PROMPT_VERSION
from mizu_kakeibo.jobs.evaluation import evaluate, load_dataset
from mizu_kakeibo.jobs.models import JobAnalysisOutput
from mizu_kakeibo.normalization import normalize_gmail_message
from mizu_kakeibo.ollama import AnalysisError, ContextOverflowError, RuntimeMetadata

METADATA = RuntimeMetadata(
    runtime="ollama",
    runtime_version="0.40.5",
    model="qwen3.5:4b-q4_K_M",
    model_digest="sha256-test",
    prompt_version=JOBS_PROMPT_VERSION,
    temperature=0,
    seed=42,
    context_size=12288,
    max_generated_tokens=512,
)


def output(decision, company=None, job_title=None, status=None, missing_fields=()):
    application = None
    if decision == "status_update":
        application = {"company": company, "job_title": job_title, "status": status}
    return JobAnalysisOutput.model_validate_json(
        json.dumps(
            {
                "contract_version": JOB_ANALYSIS_OUTPUT_CONTRACT,
                "decision": decision,
                "reason": "Fictional evaluation output",
                "application": application,
                "missing_fields": list(missing_fields),
            }
        )
    )


def expected_output(item):
    expected = item["expected"]
    application = expected["application"] or {}
    return output(expected["decision"], **application, missing_fields=expected["missing_fields"])


def invalid_output():
    error = AnalysisError("analyser returned malformed JSON or violated the output contract")
    error.__cause__ = json.JSONDecodeError("bad", "", 0)
    return error


def operational_failure():
    error = AnalysisError("analyser runtime request failed")
    error.__cause__ = httpx.ConnectError("refused")
    return error


class ScriptedAnalyser:
    """Returns scripted results per message, keyed by received time, one per run."""

    def __init__(self, script):
        self.script = {received_at: list(results) for received_at, results in script.items()}
        self.inputs = []

    def analyse(self, analyser_input):
        self.inputs.append(analyser_input)
        result = self.script[analyser_input.message.received_at].pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def verify_runtime(self):
        return METADATA


def ticking_clock():
    ticks = count()
    return lambda: next(ticks) / 10


def script_for(data, runs):
    """Answer with the ground truth on every run, except where `runs` overrides a message."""
    loaded = load_dataset(data)
    script = {}
    for raw, loaded_example in zip(data["examples"], loaded.examples):
        message_id = loaded_example.message.source.message_id
        script[loaded_example.message.received_at] = [
            overrides.get(message_id, expected_output(raw)) for overrides in runs
        ]
    return loaded, ScriptedAnalyser(script)


GROUNDING_FAILURE = output(
    "status_update", "Pinecrest Foods", "Senior Account Executive", "applied"
)
RUN_ONE = {"job-12": GROUNDING_FAILURE, "job-13": invalid_output()}
RUN_TWO = {
    "job-06": output("status_update", "Lumen Works", "Backend Developer", "rejected"),
    "job-09": output("status_update", "Kestrel Analytics Sdn. Bhd.", "Data Analyst", "applied"),
    "job-10": output("status_update", "Nimbus Retail", "Cloud Architect", "applied"),
    "job-11": output("not_application"),
    "job-12": GROUNDING_FAILURE,
    "job-13": output("needs_review", missing_fields=["message_content"]),
}


def test_synthetic_set_end_to_end_scores_every_measure():
    data, analyser = script_for(dataset(), [RUN_ONE, RUN_TWO])

    report = evaluate(data, analyser, runs=2, clock=ticking_clock())
    summary = report["summary"]
    first, second = summary["runs"]

    assert len(analyser.inputs) == 32
    assert first["real"] == {
        "examples": 13,
        "correct_decisions": 11,
        "false_status_updates": 0,
        "missed_status_updates": 0,
        "status_updates_sent_to_review": 1,
        "correct_reviews": 1,
        "unnecessary_reviews": 0,
        "missed_reviews": 0,
        "grounding_replacements": 1,
        "invalid_outputs": 1,
        "operational_failures": 0,
        "context_overflows": 0,
        "expected_status_updates": 9,
        "extracted_status_updates": 8,
        "field_accuracy": {"company": 8, "job_title": 8, "status": 8},
        "exact_applications": 8,
        "confusion": {
            "needs_review->needs_review": 1,
            "not_application->invalid_output": 1,
            "not_application->not_application": 2,
            "status_update->needs_review": 1,
            "status_update->status_update": 8,
        },
        "applications": {
            "expected_applications": 4,
            "observed_applications": 3,
            "correct": 2,
            "wrong_status": 1,
            "merged": 0,
            "extra": 0,
            "split": 0,
            "missing": 1,
            "unlinked_status_updates": 2,
            "review_items": {"analyser": 2, "likely_match": 1, "contradictory_outcome": 0},
        },
    }
    synthetic = {
        "examples": 3,
        "correct_decisions": 3,
        "false_status_updates": 0,
        "missed_status_updates": 0,
        "status_updates_sent_to_review": 0,
        "correct_reviews": 0,
        "unnecessary_reviews": 0,
        "missed_reviews": 0,
        "grounding_replacements": 0,
        "invalid_outputs": 0,
        "operational_failures": 0,
        "context_overflows": 0,
        "expected_status_updates": 3,
        "extracted_status_updates": 3,
        "field_accuracy": {"company": 3, "job_title": 3, "status": 3},
        "exact_applications": 3,
        "confusion": {"status_update->status_update": 3},
        "applications": {
            "expected_applications": 1,
            "observed_applications": 1,
            "correct": 1,
            "wrong_status": 0,
            "merged": 0,
            "extra": 0,
            "split": 0,
            "missing": 0,
            "unlinked_status_updates": 1,
            "review_items": {"analyser": 0, "likely_match": 0, "contradictory_outcome": 1},
        },
    }
    assert first["synthetic"] == synthetic
    assert second["synthetic"] == synthetic
    assert second["real"] == {
        "examples": 13,
        "correct_decisions": 8,
        "false_status_updates": 2,
        "missed_status_updates": 0,
        "status_updates_sent_to_review": 1,
        "correct_reviews": 0,
        "unnecessary_reviews": 1,
        "missed_reviews": 1,
        "grounding_replacements": 1,
        "invalid_outputs": 0,
        "operational_failures": 0,
        "context_overflows": 0,
        "expected_status_updates": 9,
        "extracted_status_updates": 8,
        "field_accuracy": {"company": 8, "job_title": 7, "status": 8},
        "exact_applications": 7,
        "confusion": {
            "needs_review->not_application": 1,
            "not_application->needs_review": 1,
            "not_application->status_update": 2,
            "status_update->needs_review": 1,
            "status_update->status_update": 8,
        },
        "applications": {
            "expected_applications": 4,
            "observed_applications": 5,
            "correct": 1,
            "wrong_status": 1,
            "merged": 0,
            "extra": 1,
            "split": 1,
            "missing": 1,
            "unlinked_status_updates": 2,
            "review_items": {"analyser": 2, "likely_match": 1, "contradictory_outcome": 0},
        },
    }
    assert summary["consistency"] == {
        "real": {
            "examples": 13,
            "decision_consistent": 9,
            "output_consistent": 8,
            "application_lists_identical": False,
        },
        "synthetic": {
            "examples": 3,
            "decision_consistent": 3,
            "output_consistent": 3,
            "application_lists_identical": True,
        },
    }
    assert summary["error_categories"] == {
        "false_status_update": 2,
        "grounding_replaced": 1,
        "invalid_output": 1,
        "job_title_mismatch": 1,
        "missed_review": 1,
        "status_update_sent_to_review": 1,
        "unnecessary_review": 1,
    }
    assert summary["metadata"]["contract_version"] == "jobs-v0"
    assert summary["metadata"]["prompt_version"] == JOBS_PROMPT_VERSION
    assert summary["metadata"]["analysis_output_contract"] == "job-analysis-output/v0"
    assert summary["metadata"]["runs"] == 2
    assert summary["latency_ms"]["min"] == pytest.approx(100.0)

    by_id = {entry["message_id"]: entry for entry in report["examples"]}
    assert by_id["job-01"]["replay"] == ["created", "created"]
    assert by_id["job-03"]["replay"] == ["advanced", "advanced"]
    assert by_id["job-04"]["replay"] == ["unchanged", "unchanged"]
    assert by_id["job-07"]["replay"] == ["review:likely_match"] * 2
    assert by_id["job-08"]["replay"] == ["reopened", "unchanged"]
    assert by_id["job-12"]["observed"] == ["needs_review", "needs_review"]
    assert by_id["job-12"]["grounding_replaced"] == [True, True]
    assert by_id["job-13"]["observed"] == ["invalid_output", "needs_review"]
    assert by_id["job-13"]["replay"] == [None, "review:analyser"]
    assert by_id["syn-03"]["replay"] == ["review:contradictory_outcome"] * 2
    assert report["applications"][0]["synthetic"] == [
        {
            "id": 1,
            "status": "offered",
            "message_ids": ["syn-01", "syn-02"],
            "expected_keys": ["S01"],
        }
    ]


def test_examples_are_normalized_preferring_html():
    data, analyser = script_for(dataset(), [{}])
    raw = dataset()["examples"][3]["message"]
    assert "Data Analyst" not in normalize_gmail_message(raw).content.text

    evaluate(data, analyser)

    viewed = next(item for item in analyser.inputs if "viewed" in item.message.subject)
    assert "Data Analyst" in viewed.message.text
    assert viewed.contract_version == "job-analysis-input/v0"


def test_failures_count_separately_and_never_change_applications():
    data, analyser = script_for(
        dataset(),
        [
            {
                "job-01": ContextOverflowError("prompt exceeded the analyser context budget"),
                "job-03": operational_failure(),
                "job-04": invalid_output(),
            }
        ],
    )

    run = evaluate(data, analyser)["summary"]["runs"][0]["real"]

    assert (run["context_overflows"], run["operational_failures"], run["invalid_outputs"]) == (
        1,
        1,
        1,
    )
    # With every A01 update failed, A01 is missing rather than created from a failure.
    assert run["applications"]["missing"] == 1
    assert run["applications"]["correct"] == 2
    assert run["applications"]["review_items"]["analyser"] == 1


def merged_dataset():
    """Give the Pinecrest email a Lumen Works mention, so a wrong extraction is grounded."""
    data = dataset()
    data["examples"][11]["message"] = raw_message(
        "job-12",
        12,
        "Pinecrest Foods <careers@pinecrest.example.invalid>",
        "Application received",
        "Pinecrest Foods has received your application for Account Executive. "
        "Also hiring: Backend Engineer at Lumen Works.",
    )
    return data


def test_two_expected_applications_linked_together_are_merged():
    data, analyser = script_for(
        merged_dataset(),
        [{"job-12": output("status_update", "Lumen Works", "Backend Engineer", "applied")}],
    )

    run = evaluate(data, analyser)["summary"]["runs"][0]["real"]["applications"]

    # A02 and A04 share one observed application, so neither is missing or correct.
    assert run["merged"] == 1
    assert run["missing"] == 0
    assert run["correct"] == 1
    assert run["unlinked_status_updates"] == 1


def test_summary_excludes_message_ids_names_and_reasons():
    data, analyser = script_for(dataset(), [{}])

    rendered = json.dumps(evaluate(data, analyser)["summary"])

    for private in ("job-0", "syn-0", "Kestrel", "Data Analyst", "A01", "Fictional evaluation"):
        assert private not in rendered


def test_dataset_rejects_another_contract_version():
    data = dataset()
    data["contract_version"] = "jobs-v1"
    with pytest.raises(ValueError, match="contract 'jobs-v1'"):
        load_dataset(data)


def test_dataset_rejects_duplicate_message_ids():
    data = dataset()
    data["examples"].append(copy.deepcopy(data["examples"][8]))
    with pytest.raises(ValidationError, match="unique"):
        load_dataset(data)


def test_dataset_rejects_a_synthetic_example_with_a_real_key():
    data = dataset()
    data["examples"][-1]["expected"]["application_key"] = "A01"
    with pytest.raises(ValidationError, match="synthetic key"):
        load_dataset(data)


def test_dataset_rejects_a_real_example_with_a_synthetic_key():
    data = dataset()
    data["examples"][0]["expected"]["application_key"] = "S01"
    with pytest.raises(ValidationError, match="mixes real and synthetic"):
        load_dataset(data)


def test_dataset_rejects_a_real_example_stored_as_an_envelope():
    data = dataset()
    data["examples"].append(
        example(
            envelope("real-envelope", 20, "a@example.invalid", "Hi", "Hi"),
            other("not_application"),
        )
    )
    with pytest.raises(ValueError, match="must store the connector RAW response"):
        load_dataset(data)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data["expected_applications"].pop(), "names no expected application"),
        (
            lambda data: data["expected_applications"].append(
                {**data["expected_applications"][0], "key": "A99"}
            ),
            "has no labelled status update",
        ),
        (
            lambda data: data["expected_applications"].append(data["expected_applications"][0]),
            "keys must be unique",
        ),
        (
            lambda data: data["expected_applications"][1].update(status="rejected"),
            "replay to applied",
        ),
        (
            lambda data: data["examples"][8]["expected"].update(
                application={"company": "A", "job_title": "B", "status": "applied"}
            ),
            "required for status_update",
        ),
        (
            lambda data: data["examples"][8]["expected"].update(application_key="A01"),
            "required for status_update",
        ),
        (
            lambda data: data["examples"][0]["expected"].update(application_key=None),
            "required for status_update",
        ),
        (
            lambda data: data["examples"][9]["expected"].update(missing_fields=["company"]),
            "only an expected needs_review",
        ),
        (
            lambda data: data["examples"][10]["expected"].update(
                missing_fields=["message_content"]
            ),
            "only company, job_title, and status",
        ),
        (lambda data: data["examples"][0].pop("synthetic"), "synthetic"),
    ],
)
def test_dataset_rejects_inconsistent_labels(change, message):
    data = dataset()
    change(data)
    with pytest.raises(ValidationError, match=message):
        load_dataset(data)


def test_jobs_evaluate_command_writes_private_report_and_prints_summary(
    tmp_path, monkeypatch, capsys
):
    dataset_path = tmp_path / "private-jobs-eval.json"
    report_path = tmp_path / "private-jobs-report.json"
    dataset_path.write_text(json.dumps(dataset()), encoding="utf-8")
    _, analyser = script_for(dataset(), [{}, {}, {}])

    class Context:
        def __enter__(self):
            return analyser

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(cli, "OllamaJobAnalyser", Context)

    assert cli.main(["jobs-evaluate", str(dataset_path), "--output", str(report_path)]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["metadata"]["runs"] == 3
    assert summary["runs"][2]["synthetic"]["applications"]["correct"] == 1
    assert summary["consistency"]["real"]["application_lists_identical"] is True
    assert "job-01" not in json.dumps(summary)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["examples"][0]["message_id"] == "job-01"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "private-jobs-eval.json",
        "private-jobs-report.json",
    ]
