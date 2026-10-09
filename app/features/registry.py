from __future__ import annotations

from collections.abc import Callable, Mapping

from .specs import PRODUCTION_SPECS
from .windows import FEATURE_SET_VERSION, FeatureSpec, LLM_SOURCES


class FeatureRegistry:
    """Reviewed-code registry. Production features cannot be registered by an LLM."""

    def __init__(self):
        self._specs: dict[str, FeatureSpec] = {}
        self._builders: dict[str, Callable] = {}

    def register(self, spec: FeatureSpec, builder: Callable | None = None, *, source: str = 'reviewed_code'):
        src = (source or '').strip().lower()
        if spec.production and src in LLM_SOURCES:
            raise PermissionError('LLMs cannot register production features; promote only via reviewed code')
        if spec.production and src in {'candidate_pipeline', 'candidate', 'proposed'}:
            raise PermissionError('candidate pipeline cannot register production features')
        if spec.production and src not in {'reviewed_code', 'code', 'test'}:
            raise PermissionError(f'production feature {spec.name} must be registered from reviewed_code')
        existing = self._specs.get(spec.name)
        if existing and existing.production and src != 'reviewed_code':
            raise PermissionError(f'cannot overwrite production feature {spec.name}')
        self._specs[spec.name] = spec
        if builder is not None:
            self._builders[spec.name] = builder
        return spec

    def get(self, name: str) -> FeatureSpec:
        return self._specs[name]

    def names(self, *, production_only: bool = True) -> list[str]:
        items = self._specs.values()
        if production_only:
            items = [s for s in items if s.production]
        return [s.name for s in items]

    def metadata(self) -> dict:
        return {
            'feature_set_version': FEATURE_SET_VERSION,
            'features': [
                {
                    'name': s.name,
                    'entity_level': s.entity_level,
                    'version': s.version,
                    'description': s.description,
                    'dtype': s.dtype,
                    'windows': list(s.windows),
                    'uses_label_history': s.uses_label_history,
                    'production': s.production,
                    'owner': s.owner,
                    'leakage': False,
                }
                for s in self._specs.values()
            ],
        }

    def require_no_leakage(self, columns: Mapping[str, object] | list[str]):
        cols = set(columns)
        from .windows import LEAKAGE_COLUMNS
        leak = sorted(cols & LEAKAGE_COLUMNS)
        if leak:
            raise ValueError(f'leakage columns are not features: {leak}')


def production_registry() -> FeatureRegistry:
    reg = FeatureRegistry()
    for spec in PRODUCTION_SPECS:
        reg.register(spec, source='reviewed_code')
    return reg
