import type { Layer } from '@freetoshop/core-document';

export type LayerFilterKind =
  | 'all'
  | 'pixel'
  | 'text'
  | 'shape'
  | 'adjustment'
  | 'smart-object'
  | 'group'
  | 'masked'
  | 'effects';

export function layerMatchesFilter(layer: Layer, query: string, filter: LayerFilterKind): boolean {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const nameMatches = layer.name.toLocaleLowerCase().includes(normalizedQuery);
  if (!nameMatches) return false;
  if (filter === 'all') return true;
  if (filter === 'masked') return Boolean(layer.rasterMask || layer.vectorMask);
  if (filter === 'effects') return layer.effects.some((effect) => effect.enabled);
  return layer.kind === filter;
}

export function visibleLayerIdsForFilter(
  layers: readonly Layer[],
  query: string,
  filter: LayerFilterKind,
): ReadonlySet<string> {
  const visible = new Set<string>();
  const visit = (items: readonly Layer[]): boolean => {
    let subtreeMatches = false;
    for (const layer of items) {
      const selfMatches = layerMatchesFilter(layer, query, filter);
      const childMatches = layer.kind === 'group' ? visit(layer.children) : false;
      if (selfMatches || childMatches) {
        visible.add(layer.id);
        subtreeMatches = true;
        if (selfMatches && layer.kind === 'group') {
          const include = (children: readonly Layer[]) => {
            for (const child of children) {
              visible.add(child.id);
              if (child.kind === 'group') include(child.children);
            }
          };
          include(layer.children);
        }
      }
    }
    return subtreeMatches;
  };
  visit(layers);
  return visible;
}
