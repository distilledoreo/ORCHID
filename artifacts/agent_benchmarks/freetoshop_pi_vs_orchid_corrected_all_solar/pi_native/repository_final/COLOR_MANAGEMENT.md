# Color Management

Phase A uses the browser Canvas 2D and image-codec pipeline. It does not provide
a color-managed professional print workflow or guarantee preservation of
embedded ICC profiles.

Unless a tested code path states otherwise, pixels should be treated as
standard dynamic-range sRGB-like browser output with 8-bit export behavior.
Results may vary with browser, operating system, display profile, decoder, and
GPU. CMYK, Lab, spot colors, HDR/wide gamut, soft proofing, rendering intents,
black-point compensation, linear-light compositing, and calibrated print output
are unsupported.

Image Size currently resamples owned 8-bit sRGB-like RGBA buffers. Nearest
neighbor samples source pixel centers directly. Bilinear sampling also uses
pixel-center coordinates and interpolates RGBA in premultiplied alpha before
returning straight-alpha bytes, preventing transparent RGB from creating color
fringes. It is not profile-aware or linear-light interpolation, and it is not an
area filter for high-quality large downscales. Coverage buffers for raster
masks, selections, and saved channels use scalar nearest or bilinear sampling.
There is no tiled, 16-bit, or floating-point resampling backend yet.

Current color filters also operate directly on encoded 8-bit sRGB-like bytes;
they are not ICC-aware or linear-light operations. Contrast uses the conventional
byte-domain factor around 128. Levels applies composite RGB input/output ranges
and gamma, without per-channel curves. Hue/Saturation converts encoded RGB to
HSL and applies relative saturation/lightness. Grayscale and Threshold use
0.2126/0.7152/0.0722 luminance weights on encoded values. Posterize quantizes
each channel independently. Gaussian Blur uses a separable kernel with
edge-clamped sampling and premultiplied-alpha convolution, which avoids hidden
transparent RGB fringes but does not make the blur colorimetrically linear.
The schema-v6 compositor uses correct premultiplied-alpha source-over math, but
its blend functions and typed adjustment layers currently operate on encoded
channel values rather than a profile-managed linear-light or perceptual working
space. This is deterministic, not a claim of color parity with Photoshop.

Code must not silently label unconverted values as another color space. Future
color work needs explicit profile parsing/conversion, tagged test fixtures,
numeric and visual reference tests, export metadata policy, and browser support
documentation before any fidelity claim.
