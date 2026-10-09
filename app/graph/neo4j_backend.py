from __future__ import annotations

import os

from .backend import GraphBackend, GraphBackendNotConfigured
from .schema import GraphNode, GraphSnapshot


class Neo4jGraphBackend(GraphBackend):
    """Placeholder that preserves the GraphBackend contract for a later Neo4j swap."""

    name = 'neo4j'

    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None):
        self.uri = uri or os.environ.get('NEO4J_URI')
        self.user = user or os.environ.get('NEO4J_USER', 'neo4j')
        self.password = password or os.environ.get('NEO4J_PASSWORD')
        if not self.uri:
            raise GraphBackendNotConfigured(
                'Neo4j backend is not configured. Set NEO4J_URI or use GRAPH_BACKEND=networkx'
            )

    def upsert_node(self, node_type: str, raw_id: str, properties: dict | None = None) -> str:
        raise GraphBackendNotConfigured('Neo4j upsert_node is not implemented in the MVP')

    def upsert_edge(self, source: str, rel_type: str, target: str, properties: dict | None = None) -> None:
        raise GraphBackendNotConfigured('Neo4j upsert_edge is not implemented in the MVP')

    def get_node(self, node_id: str) -> GraphNode | None:
        raise GraphBackendNotConfigured('Neo4j get_node is not implemented in the MVP')

    def resolve(self, entity_id: str) -> GraphNode | None:
        raise GraphBackendNotConfigured('Neo4j resolve is not implemented in the MVP')

    def neighbors(self, node_id: str, rel_type: str | None = None, direction: str = 'both'):
        raise GraphBackendNotConfigured('Neo4j neighbors is not implemented in the MVP')

    def subgraph(self, node_id: str, depth: int = 2) -> GraphSnapshot:
        raise GraphBackendNotConfigured('Neo4j subgraph is not implemented in the MVP')

    def snapshot(self) -> GraphSnapshot:
        raise GraphBackendNotConfigured('Neo4j snapshot is not implemented in the MVP')
