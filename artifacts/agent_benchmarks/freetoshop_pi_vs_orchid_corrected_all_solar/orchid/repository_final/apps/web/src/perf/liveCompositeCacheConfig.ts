export const DEFAULT_LIVE_COMPOSITE_CACHE_MAX_BYTES = 96 * 1024 * 1024;

/**
 * Resolves the production composite-cache budget. Browser performance tests may
 * install the global before App mounts to exercise the unchanged LRU under a
 * smaller deterministic budget; ordinary sessions always use the default.
 */
export function resolveLiveCompositeCacheMaxBytes(candidate: unknown): number {
  return Number.isSafeInteger(candidate) && Number(candidate) > 0
    ? Number(candidate)
    : DEFAULT_LIVE_COMPOSITE_CACHE_MAX_BYTES;
}

declare global {
  interface Window {
    __freetoshopPerfCacheMaxBytes?: number;
  }
}
