"""Unit tests for the framework's safety review layer -- failure must always lean conservative.

This is the one module in the framework where getting it wrong causes an
incident, so the focus here isn't feature coverage but **every failure path
lands on the safe side**: an unrecognized verdict, a broken regex, a reviewer
that crashes -- all of them must demand confirmation, never silently allow.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent.safety import (  # noqa: E402
    Review,
    Rule,
    Verdict,
    fallback_decision,
    scan,
)

RULES = [
    {"id": "rm", "pattern": r"\brm\b", "label": "delete file", "risk": "high"},
    {"id": "chmod", "pattern": r"\bchmod\b", "label": "change permissions", "risk": "medium"},
]


# ── Failure direction: always lean conservative ──────────────────────────────

def test_unknown_verdict_becomes_need_confirm():
    """The reviewer returned an unrecognized word -- it must never be treated as an allow."""
    for raw in ["", None, "yes", "approve", "OK", "allow_it", 42, {}]:
        assert Verdict.parse(raw) is Verdict.NEED_CONFIRM, raw
    print("test_unknown_verdict_becomes_need_confirm OK")


def test_known_verdicts_parse_exactly():
    assert Verdict.parse("allow") is Verdict.ALLOW
    assert Verdict.parse(" allow ") is Verdict.ALLOW              # tolerates whitespace
    assert Verdict.parse("withdraw_and_rethink") is Verdict.WITHDRAW
    assert Verdict.parse("dangerous_without_confirm") is Verdict.DANGEROUS
    print("test_known_verdicts_parse_exactly OK")


def test_fallback_never_allows():
    """The verdict when the reviewer is unavailable (timeout / error / no key)."""
    r = fallback_decision("rm -rf /", [{"id": "rm"}], "reviewer timed out")
    assert r.verdict is Verdict.NEED_CONFIRM
    assert not r.allowed and r.needs_user
    assert r.reason and r.advice                                  # has something to say to both user and agent
    print("test_fallback_never_allows OK")


def test_broken_regex_does_not_disable_the_rest():
    """One rule written badly must not stop the others from working -- otherwise the safety layer fails silently."""
    rules = [{"id": "bad", "pattern": "[unclosed", "label": "x"}] + RULES
    assert {m["id"] for m in scan("rm -rf /", rules)} == {"rm"}
    print("test_broken_regex_does_not_disable_the_rest OK")


# ── Scanning ──────────────────────────────────────────────────────────────────

def test_scan_matches_case_insensitively():
    assert scan("RM -rf /", RULES) and scan("rm -rf /", RULES)
    print("test_scan_matches_case_insensitively OK")


def test_scan_empty_and_harmless():
    assert scan("", RULES) == [] and scan("   ", RULES) == []
    assert scan("ls -la", RULES) == []
    print("test_scan_empty_and_harmless OK")


def test_scan_reports_all_matches_with_metadata():
    matched = scan("rm -rf /a && chmod 777 /b", RULES)
    assert {m["id"] for m in matched} == {"rm", "chmod"}
    assert all(m["label"] and m["risk"] and m["pattern"] for m in matched)
    print("test_scan_reports_all_matches_with_metadata OK")


def test_scan_accepts_rule_objects_too():
    matched = scan("rm x", [Rule(id="rm", pattern=r"\brm\b", label="delete", risk="high")])
    assert matched[0]["id"] == "rm" and matched[0]["risk"] == "high"
    print("test_scan_accepts_rule_objects_too OK")


def test_scan_skips_rules_without_pattern():
    assert scan("rm x", [{"id": "empty", "pattern": "", "label": "x"}]) == []
    print("test_scan_skips_rules_without_pattern OK")


# ── Semantics of Review ───────────────────────────────────────────────────────

def test_withdraw_does_not_bother_the_user():
    """A withdraw-and-rethink is the agent changing its approach, not the user rejecting it -- it must not pop up a dialog."""
    r = Review(verdict=Verdict.WITHDRAW, advice="run ls first to confirm the scope")
    assert not r.needs_user and not r.allowed
    print("test_withdraw_does_not_bother_the_user OK")


def test_allow_and_need_confirm_flags():
    assert Review(verdict=Verdict.ALLOW).allowed
    assert Review(verdict=Verdict.NEED_CONFIRM).needs_user
    assert not Review(verdict=Verdict.DANGEROUS).needs_user   # rejected outright, no confirmation asked
    print("test_allow_and_need_confirm_flags OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} SAFETY TESTS PASSED")
