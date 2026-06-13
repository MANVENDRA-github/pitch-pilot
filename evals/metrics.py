"""Metrics for the pitch-pilot eval harness.

Pure functions that turn a list of per-company result records (the JSON objects the
runner checkpoints to ``evals/results/<run_id>.jsonl``) into the headline numbers
the project is judged on. Nothing here touches the network or the filesystem — it
is all deterministic arithmetic over the records, so it is trivially testable.

A result record (one per company, ``status == "ok"``) carries at least:

* ``label`` — the human ground-truth, ``"qualified"`` or ``"not_qualified"``;
* ``predicted_qualified`` — what the pipeline decided (bool);
* ``score`` — the qualification fit score;
* ``draft_passed`` — whether the draft cleared the verify gate (``None`` if the
  company was disqualified and never drafted);
* ``groundedness_score`` / ``faithfulness_score`` — the verify scores (``None`` if
  no draft was attempted);
* ``flagged_claims`` — verify failure lines (``"<reason>: <claim>"``);
* ``tier_breakdown`` — claims per backing source tier;
* ``fact_count`` — facts gathered for the company;
* ``category`` — ``"good_fit"`` / ``"bad_fit"`` / ``"sparse"``.

Records with ``status == "error"`` are excluded from every metric (a company we
could not evaluate must not silently count as a wrong prediction).
"""

from __future__ import annotations

from collections import Counter

# The verify gate's failure reasons, in reporting order. Counting these proves the
# gate *catches* bad claims, not just that it passes good ones.
FAILURE_REASONS = ("unbacked", "volatile-source", "not-substring", "overreach", "unsupported")


def ok_results(results: list[dict]) -> list[dict]:
    """Return only the records we could actually evaluate (``status == "ok"``)."""
    return [r for r in results if r.get("status") == "ok"]


def _safe_div(numerator: float, denominator: float) -> float:
    """Divide, returning ``0.0`` when the denominator is zero."""
    return numerator / denominator if denominator else 0.0


def confusion(results: list[dict]) -> dict[str, int]:
    """Confusion counts for the positive class ``qualified``.

    Args:
        results: Per-company records (only ``status == "ok"`` are counted).

    Returns:
        A dict with ``tp`` / ``fp`` / ``tn`` / ``fn`` / ``n``, comparing
        ``predicted_qualified`` against ``label == "qualified"``.
    """
    tp = fp = tn = fn = 0
    for r in ok_results(results):
        actual = r.get("label") == "qualified"
        predicted = bool(r.get("predicted_qualified"))
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and not actual:
            tn += 1
        else:
            fn += 1
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "n": tp + fp + tn + fn}


def qualification_metrics(results: list[dict]) -> dict[str, float | int]:
    """Accuracy, precision, recall, and F1 for qualification.

    Precision/recall are for the positive class ``qualified``. Returns ``0.0`` for
    any metric whose denominator is zero (e.g. no positive predictions).
    """
    c = confusion(results)
    tp, fp, tn, fn, n = c["tp"], c["fp"], c["tn"], c["fn"], c["n"]
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall)
    accuracy = _safe_div(tp + tn, n)
    return {
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        **c,
    }


def draft_pass_rate(results: list[dict]) -> dict[str, float | int]:
    """Of the leads the pipeline qualified, the share that produced a passing draft.

    ``attempted`` counts companies that were drafted (``draft_passed`` is not
    ``None`` — i.e. predicted qualified); ``passed`` counts those whose draft
    cleared the verify gate.
    """
    attempted = [r for r in ok_results(results) if r.get("draft_passed") is not None]
    passed = sum(1 for r in attempted if r.get("draft_passed"))
    return {
        "attempted": len(attempted),
        "passed": passed,
        "pass_rate": round(_safe_div(passed, len(attempted)), 4),
    }


def mean_scores(results: list[dict]) -> dict[str, float | int]:
    """Mean groundedness and faithfulness scores over companies that were drafted."""
    drafted = [r for r in ok_results(results) if r.get("groundedness_score") is not None]
    g = [float(r["groundedness_score"]) for r in drafted]
    f = [float(r.get("faithfulness_score") or 0.0) for r in drafted]
    return {
        "n_drafted": len(drafted),
        "mean_groundedness": round(_safe_div(sum(g), len(g)), 4),
        "mean_faithfulness": round(_safe_div(sum(f), len(f)), 4),
    }


def failure_modes(results: list[dict]) -> dict[str, int]:
    """Count verify failures by reason across all flagged claims.

    Each ``flagged_claims`` entry is ``"<reason>: <claim>"``; the reason prefix is
    counted. Returns a count for every reason in `FAILURE_REASONS` (zeros included)
    so the report table is stable.
    """
    counts: Counter[str] = Counter()
    for r in ok_results(results):
        for flag in r.get("flagged_claims", []):
            reason = str(flag).split(":", 1)[0].strip()
            counts[reason] += 1
    return {reason: counts.get(reason, 0) for reason in FAILURE_REASONS}


def facts_by_category(results: list[dict]) -> dict[str, float]:
    """Mean fact count per company, grouped by dataset category.

    This is the *degradation* metric: it shows how much thinner research is for
    ``sparse`` companies than for ``good_fit`` ones.
    """
    buckets: dict[str, list[int]] = {}
    for r in ok_results(results):
        buckets.setdefault(r.get("category", "unknown"), []).append(int(r.get("fact_count", 0)))
    return {cat: round(_safe_div(sum(counts), len(counts)), 2) for cat, counts in sorted(buckets.items())}


def aggregate(results: list[dict], recheck: dict | None = None) -> dict:
    """Compute the full set of headline metrics from result records.

    Args:
        results: Per-company result records.
        recheck: Optional live re-verifiability summary (``{tier: {...}}``) from the
            ``recheck`` command, included verbatim under ``live_reverifiability``.

    Returns:
        A dict of all metric blocks, suitable for the report and the console summary.
    """
    ok = ok_results(results)
    return {
        "n_total": len(results),
        "n_ok": len(ok),
        "n_error": len(results) - len(ok),
        "qualification": qualification_metrics(results),
        "draft_gate": draft_pass_rate(results),
        "scores": mean_scores(results),
        "failure_modes": failure_modes(results),
        "facts_by_category": facts_by_category(results),
        "live_reverifiability": recheck or {},
    }
