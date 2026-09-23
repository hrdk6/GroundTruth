"""Version detection and cross-version conflict comparison.

Detection decides the SQL pre-filter for every query, so a false negative
silently answers from the wrong release and a false positive silently narrows
retrieval to one. Both directions get cases.
"""

from __future__ import annotations

import pytest

from app.retrieval.versioning import closest_counterparts, detect_versions, resolve_version

AVAILABLE = ["1.26", "1.28", "1.30"]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("How do sidecar containers work in Kubernetes 1.28?", ["1.28"]),
        ("What changed in v1.26?", ["1.26"]),
        ("k8s 1.30 pod security admission", ["1.30"]),
        ("release-1.28 notes", ["1.28"]),
        ("Is this in 1.28.3 yet?", ["1.28"]),
        # Bare releases: PROJECT_SPEC.md S8.4 lists "1.28" as explicit, and
        # requiring a cue word answered this question from the latest release.
        ("How do sidecars work in 1.28?", ["1.28"]),
        ("Compare 1.26 and 1.30 behaviour", ["1.26", "1.30"]),
    ],
)
def test_versions_are_detected(question: str, expected: list[str]) -> None:
    assert detect_versions(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "Should a pod request 1.5 GB of memory?",
        "Give the container 1.25 CPUs",
        "Is 1.2 cores enough?",
        "Set the limit to 1.50 GiB",
        "It got 1.10x faster",
        "Scale to 1.20% of nodes",
        "What does pi = 3.14 have to do with it?",
        "Latency rose by 1.30 ms",
    ],
)
def test_quantities_are_not_versions(question: str) -> None:
    assert detect_versions(question) == []


def test_named_version_wins_over_the_default() -> None:
    decision = resolve_version("How does this work in 1.26?", available=AVAILABLE)
    assert decision.version == "1.26"
    assert decision.explicit


def test_unindexed_version_falls_back_to_latest_and_says_so() -> None:
    decision = resolve_version("What changed in Kubernetes 1.42?", available=AVAILABLE)
    assert decision.version == "1.30"
    assert not decision.explicit
    assert "1.42" in decision.reason


def test_explicit_argument_wins_over_the_question() -> None:
    decision = resolve_version("In 1.26, how...", available=AVAILABLE, requested="1.28")
    assert decision.version == "1.28"


def test_no_version_uses_latest_numerically() -> None:
    decision = resolve_version("What is a Pod?", available=["1.9", "1.10"])
    assert decision.version == "1.10", "1.10 is newer than 1.9 despite sorting lower as text"


# ---------------------------------------------------------------------------
# Conflict comparison
# ---------------------------------------------------------------------------
SECTION_PART_1 = "The kubelet restarts a container when its liveness probe fails. " * 5
SECTION_PART_2 = "Readiness probes decide whether a Pod receives traffic from Services. " * 5


def test_a_split_section_is_not_a_conflict_with_itself() -> None:
    """Regression: chunk 1 was compared against chunk 2 of the same section.

    A long section becomes several chunks under one heading, in every version.
    Comparing a chunk to each of them made every such section a "conflict".
    """
    closest = closest_counterparts(
        SECTION_PART_1, [(SECTION_PART_1, "1.26"), (SECTION_PART_2, "1.26")]
    )
    similarity, text = closest["1.26"]
    assert similarity == pytest.approx(1.0)
    assert text == SECTION_PART_1


def test_a_genuinely_changed_section_is_still_detected() -> None:
    changed = SECTION_PART_1.replace("restarts", "kills and recreates")
    closest = closest_counterparts(SECTION_PART_1, [(changed, "1.26"), (SECTION_PART_2, "1.26")])
    similarity, _ = closest["1.26"]
    assert similarity < 1.0


def test_each_other_version_gets_its_own_counterpart() -> None:
    closest = closest_counterparts(SECTION_PART_1, [(SECTION_PART_1, "1.26"), ("x" * 50, "1.28")])
    assert set(closest) == {"1.26", "1.28"}
    assert closest["1.26"][0] > closest["1.28"][0]
