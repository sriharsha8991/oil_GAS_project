"""Tests for the post-extraction entity normalizer (G-L1.3) and numeric
guardrail (G-L1.6)."""

from __future__ import annotations

from app.pipeline.entity_normalizer import (
    normalize_entities,
    should_force_escalation as normalizer_force_escalate,
)
from app.pipeline.numeric_guardrail import (
    check_entities,
    should_force_escalation as guardrail_force_escalate,
    summarize_flags_for_gap,
)


# ---------------------------------------------------------------------------
# Entity normalizer
# ---------------------------------------------------------------------------


def test_normalize_passes_through_clean_entities():
    raw = [
        {"type": "depth", "value": "3450", "unit": "ft", "context": "TD"},
        {"type": "well_id", "value": "ALP-12", "unit": "", "context": ""},
        {
            "type": "npt_event",
            "value": "stuck_pipe",
            "unit": "hours",
            "context": "trip in",
        },
    ]
    norm, warns = normalize_entities(raw)
    assert len(norm) == 3
    assert warns == []
    assert norm[0]["type"] == "depth"
    assert norm[2]["value"] == "stuck_pipe"


def test_normalize_lowercases_known_types():
    norm, warns = normalize_entities([{"type": "DEPTH", "value": "100", "unit": "ft"}])
    assert norm[0]["type"] == "depth"
    assert warns == []


def test_normalize_coerces_unknown_type_to_other():
    norm, warns = normalize_entities([{"type": "spaceship", "value": "foo"}])
    assert norm[0]["type"] == "other"
    assert norm[0]["type_raw"] == "spaceship"
    assert any("spaceship" in w for w in warns)


def test_normalize_coerces_unknown_npt_value():
    norm, warns = normalize_entities([
        {"type": "npt_event", "value": "alien_invasion", "unit": "hours"}
    ])
    assert norm[0]["value"] == "other"
    assert norm[0]["value_raw"] == "alien_invasion"
    assert any("alien_invasion" in w for w in warns)


def test_normalize_coerces_unknown_operation_value():
    norm, warns = normalize_entities([
        {"type": "operation", "value": "moon_landing"}
    ])
    assert norm[0]["value"] == "other"
    assert any("moon_landing" in w for w in warns)


def test_normalize_validates_depth_type():
    norm, warns = normalize_entities([
        {"type": "depth", "value": "100", "unit": "ft", "depth_type": "MD"},
        {"type": "depth", "value": "200", "unit": "ft", "depth_type": "GIBBERISH"},
    ])
    assert norm[0]["depth_type"] == "MD"
    assert norm[1]["depth_type"] == ""
    assert norm[1]["depth_type_raw"] == "GIBBERISH"
    assert any("GIBBERISH" in w for w in warns)


def test_normalize_drops_non_dict_entries():
    norm, warns = normalize_entities([{"type": "depth", "value": "1"}, "junk", 42])
    assert len(norm) == 1
    assert len(warns) == 2


def test_normalize_handles_empty():
    assert normalize_entities([]) == ([], [])
    assert normalize_entities(None) == ([], [])


def test_normalizer_force_escalation_threshold():
    # one warning is tolerated, two is not
    assert normalizer_force_escalate([]) is False
    assert normalizer_force_escalate(["w1"]) is False
    assert normalizer_force_escalate(["w1", "w2"]) is True


# ---------------------------------------------------------------------------
# Numeric guardrail
# ---------------------------------------------------------------------------


def test_guardrail_passes_sane_values_with_baseline():
    baseline = "Total depth was 3450 ft and shoe pressure 12000 psi."
    flags = check_entities(
        [
            {"type": "depth", "value": "3450", "unit": "ft"},
            {"type": "pressure", "value": "12000", "unit": "psi"},
        ],
        baseline_text=baseline,
    )
    assert flags == []


def test_guardrail_flags_out_of_range_pressure():
    flags = check_entities(
        [{"type": "pressure", "value": "999999", "unit": "psi"}],
        baseline_text="999999 psi appears in baseline",  # baseline match irrelevant
    )
    assert len(flags) == 1
    assert flags[0]["kind"] == "value_out_of_range"


def test_guardrail_flags_out_of_range_mud_weight():
    flags = check_entities(
        [{"type": "mud_weight", "value": "100", "unit": "ppg"}],
    )
    assert len(flags) == 1
    assert flags[0]["kind"] == "value_out_of_range"


def test_guardrail_flags_missing_from_baseline():
    flags = check_entities(
        [{"type": "depth", "value": "9999", "unit": "ft"}],
        baseline_text="Drilled to 3450 ft on 2024-01-01.",
    )
    assert len(flags) == 1
    assert flags[0]["kind"] == "numeric_not_in_baseline"


def test_guardrail_accepts_comma_grouped_baseline():
    flags = check_entities(
        [{"type": "depth", "value": "12000", "unit": "ft"}],
        baseline_text="Reached 12,000 ft TD",
    )
    assert flags == []


def test_guardrail_skips_baseline_check_when_scanned():
    flags = check_entities(
        [{"type": "depth", "value": "9999", "unit": "ft"}],
        baseline_text="",            # scanned page → no baseline
        is_scanned=True,
    )
    assert flags == []


def test_guardrail_skips_baseline_when_baseline_empty():
    flags = check_entities(
        [{"type": "depth", "value": "9999", "unit": "ft"}],
        baseline_text="",
        is_scanned=False,
    )
    assert flags == []


def test_guardrail_skips_non_numeric_types():
    flags = check_entities(
        [
            {"type": "well_id", "value": "ALP-12"},
            {"type": "formation", "value": "Eagle Ford"},
            {"type": "date", "value": "2024-01-01"},
        ],
        baseline_text="anything",
    )
    assert flags == []


def test_guardrail_handles_empty_input():
    assert check_entities([]) == []
    assert check_entities(None) == []


def test_guardrail_force_escalation_zero_tolerance():
    assert guardrail_force_escalate([]) is False
    assert guardrail_force_escalate(
        [{"kind": "value_out_of_range", "type": "depth", "value": 1, "unit": "ft"}]
    ) is True


def test_summarize_flags_truncates():
    flags = [
        {"kind": "value_out_of_range", "type": "depth", "value": i, "unit": "ft"}
        for i in range(12)
    ]
    summary = summarize_flags_for_gap(flags)
    assert "numeric guardrail" in summary
    assert "+4 more" in summary
