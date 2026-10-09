from __future__ import annotations

import os

from .backend import GraphBackend
from .neo4j_backend import Neo4jGraphBackend
from .networkx_backend import NetworkXGraphBackend


def create_backend(name: str | None = None) -> GraphBackend:
    backend = (name or os.environ.get('GRAPH_BACKEND') or 'networkx').strip().lower()
    if backend in {'networkx', 'nx', 'memory'}:
        return NetworkXGraphBackend()
    if backend in {'neo4j', 'cypher'}:
        return Neo4jGraphBackend()
    raise ValueError(f'unsupported GRAPH_BACKEND: {backend}')
