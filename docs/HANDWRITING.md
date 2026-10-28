# Handwriting engine

## Honest summary

The shipped engine is **glyph compositing**: it reuses the letters you actually wrote. It is not a trained
handwriting-synthesis model, and nothing is trained on your samples. Fidelity therefore depends directly on
sample coverage and quality:

| Source | Letter shapes | Notes |
|---|---|---|
| Template sheet | Exact (cut from labelled boxes) | Best result. Up to 3 variants per lowercase letter. |
| Free-form, separate letters | Good (letters matched to the transcript) | Needs a transcript that matches line by line. |
| Free-form, joined/cursive | Approximate (letters cut at thin joins) | Some cuts are wrong; AI verification removes most of them. |
| Missing character | Derived (u↔n rotated, p↔q, b↔d mirrored; c o v w x z capitals scaled) or fallback font | Always listed in the profile report and render warnings. |

## Building a profile

1. **Load & correct** (`preprocess.py`): EXIF orientation, user crop/rotation, size limits (decompression-bomb
   guard), quality checks (resolution, blur, contrast, ink amount). Fatal issues reject the sample with a reason.
2. **Clean** : illumination flattening (divide by a morphological background estimate) removes shadows; Otsu
   binarisation; printed ruled lines are found as periodic, tilt-corrected rows and removed with
   staff-line removal (a column's ink run is erased only if it is no thicker than the rule, so strokes that
   cross a rule survive); margin lines and notebook-edge marks are removed.
3. **Template sheets** (`template.py`, `segment.py`): four corner squares give a perspective transform; a binary
   page code fixes orientation (any of 0/90/180/270° plus perspective) and page number; each box is cropped
   inside its border, measured against the printed baseline ticks.
4. **Free-form pages**: lines come from the rules (ruled paper) or from row-ink profile peaks (unruled);
   x-height from the line's core band; slant by shear search over vertical strokes; stroke width; letter, word
   and line spacing. With a transcript, ink clusters are grouped into words (dynamic programming over gaps and
   expected word widths), and each word is split into letters (directly if its clusters match the letters,
   otherwise by cutting at the thinnest joins near expected letter boundaries). Every candidate is checked
   against its letter class (x-height letters, ascenders, descenders, widths).
5. **AI verification (optional)**: candidate letters are tiled into labelled contact sheets and the configured
   vision model returns which ones really show the labelled character. Only those survive. Your images are sent
   only for this purpose, to the provider you configured.
6. **Consistency vote**: remaining variants of a character are compared with their medoid; outliers are dropped.
   Before the vote, cut-out letters are tidied: small stray specks (a neighbour's i-dot) are removed and
   letters without descenders are sat on the baseline. Letters from blurry or low-resolution photos are used
   only when no sharp sample has that letter, and looped letters (a b d e g o p q) whose loop is filled in
   are dropped when an open variant exists (and listed in the report when none does).
7. **Library**: glyph bitmaps (with baseline and scale) and metrics are stored as a versioned JSON artifact,
   separate from your source samples. Deleting a profile deletes both.

## Rendering

- Glyph bitmaps are traced (OpenCV contours, smoothed and simplified) into **vector outlines**, defined once
  per variant as PDF form XObjects and placed with a transform. Ink colour, stroke weight (dilate/erode before
  tracing), slant (shear), size and rotation are all applied as vector operations; nothing is rasterised.
- Layout uses measured glyph widths: letters are spaced by their actual ink extents plus your measured gap.
- Every letter is normalised before placing: scaled so its body matches the x-height (or its class's median
  height) and thinned or thickened towards the median pen width, so letters from different photos match.
- **Joined letters** (cursive). The "Letters" style option: Automatic / Joined / Separate on a profile
  (Automatic = joined when the free-form samples were written joined), and Same as the profile / Joined /
  Separate on an assignment:
  within a word, each lowercase letter is connected to the next by a drawn joining stroke at the pen width,
  from the rightmost ink of one letter (at its lowest point, or its top for o v w b r) to the leftmost ink of
  the next at the same height, so joins run level and do not cut through round letters. No joins after
  g j q y z, capitals, digits or punctuation. The wobble is then applied per word rather than per letter so
  the joins stay aligned.
  Lines snap to the ruled baseline grid; tall content (fractions, tables, figures) takes extra rows.
- Natural variation is deterministic (seeded): variant choice without immediate repeats, slight scale,
  rotation and baseline jitter, and a smooth baseline wander per line. Same inputs + seed => identical PDF.
- Maths: a LaTeX subset (fractions, roots, scripts, Greek, operators, accents) is laid out from your glyphs
  with hand-drawn bars and radicals. Unsupported commands are written literally and reported.
- Diagrams: a ```diagram block in an answer (JSON, see `render/diagram.py`) is drawn by hand: wobbly
  ink strokes at the pen width, axes with arrow heads, markers (triangle, circle, square, cross, plus, star,
  dot), line and bar series, circles (``around_nearest: k`` encloses exactly the k nearest points, e.g. for
  KNN), lines and arrows, a legend placed in the emptiest corner, labels moved clear of markers and outlines
  (with a pointer when crowded), and a caption, all written in the user's glyphs. It is sized in x-heights,
  so it shrinks with the handwriting when an answer is fitted to its space. The answer prompt asks the model
  for such blocks when a question wants a plot on axes; other diagrams are still flagged for the student.
- Writing on source pages: the handwriting layer is composed over the original PDF page (vector on vector).
  If an answer does not fit its space, the size is reduced (down to `min_fit_scale`), then extra pages are
  added with an `exceeds_space` warning. Generation is told the measured word capacity of each space and
  answers are re-measured and shortened automatically when needed.

## Extending: a real synthesis model

`render/engine.py` defines `HandwritingEngine` (shape a run of text → positioned items + metrics). A stroke- or
diffusion-based model trained on the user's writing can implement it (likely as an optional GPU service) without
changing layout, pagination or PDF output. Such a model would need explicit user consent for training.

## Tips for good samples

- Dark pen, even light, no shadows; scanner or phone held flat; 200 dpi+ (≥ 1200 px short side).
- Fill both template pages; write naturally but keep each character inside its box, sitting on the ticks.
- For free-form pages, write separated letters if you can; keep the transcript line breaks identical to the page.
