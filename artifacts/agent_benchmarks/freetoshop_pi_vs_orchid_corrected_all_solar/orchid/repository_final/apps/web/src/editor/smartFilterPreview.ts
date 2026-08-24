import { findLayer, setSmartObjectFilters, type ImageDocument } from '@freetoshop/core-document';
import type { FilterDraft } from './filterOperations.js';
import { createSmartFilterFromDraft, supportsSmartFilter } from './operationDefinitions.js';

/**
 * Builds a temporary document preview that appends a draft smart filter to a
 * smart-object layer without committing history. Used for live dialog preview.
 */
export function previewDocumentWithSmartFilterDraft(
  document: ImageDocument,
  layerId: string,
  draft: FilterDraft,
  previewFilterId = 'smart-filter-preview',
): ImageDocument {
  const layer = findLayer(document, layerId);
  if (!layer || layer.kind !== 'smart-object') {
    throw new Error('Select a smart object to preview a smart filter.');
  }
  if (!supportsSmartFilter(draft.kind)) {
    throw new Error(`Smart filter preview is not supported for ${draft.kind}.`);
  }
  const smartFilter = createSmartFilterFromDraft(draft.kind, draft, previewFilterId);
  // Replace any previous preview filter id so slider changes do not stack.
  const withoutPreview = layer.smartFilters.filter((filter) => filter.id !== previewFilterId);
  return setSmartObjectFilters(layerId, [...withoutPreview, smartFilter]).execute(document);
}
