import { describe, expect, it } from 'vitest';
import { hitTestGuide } from './guideHitTest.js';

describe('hitTestGuide', () => {
  const guides = [
    { id: 'v', direction: 'vertical' as const, position: 40 },
    { id: 'h', direction: 'horizontal' as const, position: 20 },
  ];

  it('hits the nearest vertical guide within threshold', () => {
    const hit = hitTestGuide({ x: 42, y: 10 }, guides, 4);
    expect(hit?.guide.id).toBe('v');
    expect(hit?.distance).toBe(2);
  });

  it('hits a horizontal guide', () => {
    const hit = hitTestGuide({ x: 5, y: 22 }, guides, 4);
    expect(hit?.guide.id).toBe('h');
  });

  it('returns null outside threshold', () => {
    expect(hitTestGuide({ x: 50, y: 50 }, guides, 4)).toBeNull();
  });
});
