from __future__ import annotations

import ast
import inspect
import textwrap

from jeet_analyzer import cluster


def test_cluster_streams_parsed_evidence_without_unbounded_raw_transaction_store() -> None:
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

    assert "all_transactions" not in assigned_names
    assert "observed_transaction_signatures" in source
    assert "target_events_by_signature" in source
    assert "history.normalize_transaction(" in source
    assert "replace(seed_batch, transactions=[])" in source
    assert "replace(batch, transactions=[])" in source
    assert "len(observed_transaction_signatures)" in source
