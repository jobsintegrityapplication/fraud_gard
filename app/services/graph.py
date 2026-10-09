from ..graph.analysis import score_transaction as graph_score_impl


def graph_score(tx, df):
    """MVP fusion hook. Graph scores are risk signals, not fraud classifications."""
    return graph_score_impl(tx, df)
