import type { CoverageSource, PixelSource } from '@freetoshop/pixel-storage';

export function samplePixelThumbnail(source: PixelSource, size = 32): Uint8ClampedArray {
  const output = new Uint8ClampedArray(size * size * 4);
  const chunks = [...source.pixelChunks()];
  for (let y = 0; y < size; y += 1) {
    const sourceY = Math.min(source.height - 1, Math.floor(((y + 0.5) * source.height) / size));
    for (let x = 0; x < size; x += 1) {
      const sourceX = Math.min(source.width - 1, Math.floor(((x + 0.5) * source.width) / size));
      const chunk = chunks.find(
        (candidate) =>
          sourceX >= candidate.x &&
          sourceX < candidate.x + candidate.width &&
          sourceY >= candidate.y &&
          sourceY < candidate.y + candidate.height,
      );
      if (!chunk) continue;
      const sourceIndex = ((sourceY - chunk.y) * chunk.width + sourceX - chunk.x) * 4;
      const destination = (y * size + x) * 4;
      output.set(chunk.pixels.subarray(sourceIndex, sourceIndex + 4), destination);
    }
  }
  return output;
}

export function sampleMaskThumbnail(source: CoverageSource, size = 32): Uint8ClampedArray {
  const output = new Uint8ClampedArray(size * size * 4);
  for (let y = 0; y < size; y += 1) {
    const sourceY = Math.min(source.height - 1, Math.floor(((y + 0.5) * source.height) / size));
    for (let x = 0; x < size; x += 1) {
      const sourceX = Math.min(source.width - 1, Math.floor(((x + 0.5) * source.width) / size));
      const value = source.read({ x: sourceX, y: sourceY, width: 1, height: 1 })[0] ?? 0;
      const destination = (y * size + x) * 4;
      output[destination] = value;
      output[destination + 1] = value;
      output[destination + 2] = value;
      output[destination + 3] = 255;
    }
  }
  return output;
}
