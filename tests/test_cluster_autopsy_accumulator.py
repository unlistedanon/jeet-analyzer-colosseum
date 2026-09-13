from __future__ import annotations

import ast
import inspect
import textwrap

from jeet_analyzer import cluster


def test_cluster_releases_full_autopsy_corpus_after_seed_accounting() -> None:
    source = textwrap.dedent(inspect.getsource(cluster.run_cluster_audit))
    tree = ast.parse(source)

    assigned_names = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name)
    }
    subscript_assignment_bases = {
        target.value.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
    }

    # The old lifetime-wide full TransactionAutopsy dictionary must not return.
    assert "autopsies" not in assigned_names
    assert "autopsies" not in subscript_assignment_bases

    # Seed accounting may temporarily retain full autopsies, but they are copied
    # into compact graph facts and explicitly released before downstream walks.
    assert "seed_autopsies" in assigned_names
    assert "seed_autopsies" in subscript_assignment_bases
    assert "graph_accumulator.GraphEvidenceAccumulator()" in source
    assert "graph_evidence.extend(seed_autopsies.values())" in source
    assert "seed_autopsies.clear()" in source

    # Downstream graph work operates on compact/indexed facts rather than
    # rescanning or retaining the heavyweight TransactionAutopsy mapping.
    assert "graph_evidence.ingest(autopsy)" in source
    assert "graph_evidence.candidates_for(" in source
    assert "graph_evidence.evidence(classifications)" in source
    assert "for autopsy in autopsies.values()" not in source

    # Exhaustive autopsy serialization was an unbounded duplicate debug corpus,
    # not a required result-contract field. Keep truthful volume telemetry only.
    assert '"transaction_autopsies"' not in source
    assert '"transaction_autopsy_count"' in source
    assert '"graph_evidence_storage"' in source
    assert '"excluded_infrastructure_summary"' in source
