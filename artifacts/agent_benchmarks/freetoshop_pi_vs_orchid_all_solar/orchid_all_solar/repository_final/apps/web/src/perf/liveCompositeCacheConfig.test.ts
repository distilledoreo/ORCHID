import { describe, expect, it } from 'vitest';
import {
  DEFAULT_LIVE_COMPOSITE_CACHE_MAX_BYTES,
  resolveLiveCompositeCacheMaxBytes,
} from './liveCompositeCacheConfig.js';

describe('live composite cache configuration', () => {
  it('uses the production 96 MiB budget unless a positive safe integer override is installed', () => {
    expect(resolveLiveCompositeCacheMaxBytes(undefined)).toBe(
      DEFAULT_LIVE_COMPOSITE_CACHE_MAX_BYTES,
    );
    expect(resolveLiveCompositeCacheMaxBytes(0)).toBe(DEFAULT_LIVE_COMPOSITE_CACHE_MAX_BYTES);
    expect(resolveLiveCompositeCacheMaxBytes(-1)).toBe(DEFAULT_LIVE_COMPOSITE_CACHE_MAX_BYTES);
    expect(resolveLiveCompositeCacheMaxBytes(1.5)).toBe(DEFAULT_LIVE_COMPOSITE_CACHE_MAX_BYTES);
    expect(resolveLiveCompositeCacheMaxBytes(Number.MAX_SAFE_INTEGER + 1)).toBe(
      DEFAULT_LIVE_COMPOSITE_CACHE_MAX_BYTES,
    );
  });

  it('accepts a deterministic diagnostic budget', () => {
    expect(resolveLiveCompositeCacheMaxBytes(2 * 1024 * 1024)).toBe(2 * 1024 * 1024);
  });
});
