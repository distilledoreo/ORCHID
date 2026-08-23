# Renderer

The renderer is a deterministic projection from document state and viewport
state to visible pixels. It must not mutate the document, depend on component
mount order, or fetch network resources while drawing.

The [W3C Compositing and Blending specification](https://www.w3.org/TR/compositing-1/)
is the formula authority for its standard separable and nonseparable modes.
Adobe's public
[blend-mode descriptions](https://helpx.adobe.com/photoshop/desktop/repair-retouch/adjust-light-tone/blending-mode-descriptions.html)
define the intended behavior of the additional professional modes.

## Current contract

The current compositor still materializes complete surfaces. Tiled migration is
defined in [TILED_RENDERING.md](TILED_RENDERING.md); new renderer work must use
that storage/invalidation boundary rather than introducing another full-frame
cache.

- Respect canvas bounds, layer order, visibility, opacity, and supported
  transforms/blending where those features are present in the model.
- Clear or fully paint every frame so stale pixels cannot leak between renders.
- Preserve image aspect ratio and use a documented sampling policy.
- Handle invalid or missing resources as a controlled error or placeholder,
  never an application crash.
- Keep viewport transforms distinct from document transforms.
- Compose viewport × nested parent × layer affine matrices in Canvas order before
  drawing pixel layers in local coordinates.
- Apply enabled local raster-mask coverage to pixel and structured-layer alpha
  using mask density without mutating source RGBA or editable content.
- Rasterize vector-mask paths on pixel and structured renderable layers deterministically, apply density and
  inversion, and multiply their coverage with any raster mask without mutating
  either source.
- Apply adjustment-layer vector masks in document space so their coverage scopes
  the nondestructive effect rather than rasterizing layer content.
- Apply document-space group raster and vector masks multiplicatively to the combined result. Pass-through
  groups preserve backdrop-aware child blending and adjustments, then interpolate
  that result through mask coverage; isolated groups mask their transparent surface.
- Use deterministic premultiplied-alpha source-over equations for supported
  blend modes, including RGB-vector Hue/Saturation/Color/Luminosity operations,
  independent layer/fill opacity, clipping coverage, and isolated groups.
  Normal-only stacks retain a direct Canvas fast path.
- Apply typed adjustment layers to the accumulated backdrop without changing
  source-layer bytes. Adjustment opacity, fill opacity, clipping, and
  document-space raster-mask density scale the effect.
- Rasterize editable rectangle/ellipse shapes and multiline text layout boxes
  into owned local surfaces at composite time, then pass them through the same
  affine, mask, blend, clipping, group, display, and export pipeline as pixels.
  Browser Canvas 2D supplies glyph shaping for the current text layer; the
  headless deterministic fallback intentionally renders shape geometry only.
- Rasterize arbitrary move/line/cubic/close vector paths with even-odd fill and
  centered strokes. The deterministic CPU fallback flattens cubic segments and
  enforces a path-complexity ceiling; browser Canvas retains native Bézier output.
- Apply drop shadows behind transformed layer alpha, efficient separable
  dilation for outside strokes, and color overlays before layer compositing.
  Effect output participates in clipping, opacity, blending, display, and export
  without modifying the source raster or structured layer.
- Recursively composite an embedded smart-object source into its local canvas,
  apply its ordered enabled smart filters, then apply its outer mask/effects and
  transform and blend that result into the parent document. Invert, Grayscale,
  Gaussian Blur, and Brightness/Contrast filter immutable intermediate pixels;
  per-filter opacity interpolates the result with its immediate input. The same
  path serves display and export.
- Keep selection chrome out of the document compositor so exports never include it.
- Keep Free Transform outlines, connectors, and handles in the DOM view overlay;
  they track viewport × world geometry, retain constant CSS-pixel hit targets,
  and never enter composite/export pixels.
- Offer nonmutating read-only red, green, blue, and saved-alpha preview modes.
  RGB inspection respects the normal layer/mask/transform pipeline; alpha
  inspection renders opaque grayscale coverage at document origin.
- Keep export on the default composite render path so a visible channel preview
  cannot leak into exported pixels.
- Clip display and export to the document canvas. An applied crop relies on the
  normal affine path: translated root layers render from their unchanged local
  pixel resources into the new bounds, so cropped-away pixels are hidden rather
  than deleted.
- Keep crop rectangles in view chrome. The dashed preview must never enter
  document pixels or an exported image.
- Canvas Size and Reveal All use the same clip-and-affine contract as Crop:
  translated root placement exposes or hides unchanged layer-local resources
  inside the resulting canvas. Reveal All calculates an axis-aligned union from
  transformed layer corners; it does not inspect source alpha or mask coverage.
- Image Size is resolved before rendering. The renderer receives resized RGBA
  buffers and masks plus affine transforms adjusted to preserve proportional
  nested placement; it must not apply a second resize or discard those
  transforms.
- Draw selection chrome from the canonical arbitrary mask, not its bounds.
  `selectionOutlineSegments` emits exact axis-aligned edges around pixels whose
  coverage is at least 128, including holes and disconnected islands. This is a
  resolution-independent pixel-edge outline, not a subpixel marching-squares or
  vector contour; soft coverage below the display threshold remains active for
  editing even when it has no outline segment.
- A filter preview renders an immutable transient document through the normal
  layer transform, raster-mask, channel-preview, and export-independent display
  path. Cancel discards it; Apply commits the same bounded patch before normal
  rendering resumes. Preview chrome never enters exported pixels.

Golden-image tests should use a defined color space, fixed dimensions, and a
small documented tolerance. Pixel identity across browsers/GPUs is not promised.
Large-document tiling, GPU acceleration, HDR,
CMYK compositing, advanced cross-platform text shaping, stochastic Dissolve,
knockout and Blend If behavior, smart-filter masks and blend options,
high-bit-depth resampling, and print-proof rendering are
future work.
