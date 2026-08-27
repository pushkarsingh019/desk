# 14: A literal desk — walnut slab, real edges, and the coffee

**What to build:** The desk stops being a dark felt grid and becomes a literal
desk seen from directly above: a bounded walnut slab standing on a dark floor,
with sheets lying on it as paper. A coffee mug sits at the near-right corner
and is the connection indicator — steam while the SSE stream is live, still and
cold when it drops. It replaces the red `reconnecting…` banner.

**Blocked by:** 07, 10, 12

**Status:** done

This ticket reverses a load-bearing decision. Ticket 07 built an *infinite*
canvas and SPEC user story 9 asked for one so the user would never run out of
room. A desk seen from above has edges, and a coffee at the edge needs one to
stand on. The bound is the deliberate trade: an edge is a landmark, which a
featureless infinite plane never gave us, and the cost is that a full desk must
be curated rather than escaped from. `CONTEXT.md` and story 9 are updated to
say so rather than letting the code quietly diverge from them.

- [x] The desk is a bounded slab, 4800 × 3200 desk units, drawn as walnut with a lit bullnose edge and its own shadow falling onto a featureless floor
- [x] Panning stops at the edge, with at most 160px of floor showing past it; there is no rubber-band, because a bounce implies the desk moved on its own
- [x] Dragging or resizing a sheet stops at the edge — paper does not hang off a desk
- [x] A sheet left off the slab by the previous unbounded desk stays reachable and is not moved by anything except the user dragging it
- [x] Sheets render as white paper with contact-darkening shadows, and no material, ring, or highlight ever paints over `.sheet-body`
- [x] The wood cross-fades out above 2× zoom, so the grain plate is not magnified into mush exactly when the user leans in to read a figure
- [x] The coffee steams while the stream is connected and goes cold and still when it drops, over a second rather than snapping, so a brief blip does not flash
- [x] An outage lasting 20s escalates the cup to an unmistakable alarm, because a quiet cup is a quieter alarm than the banner it replaces
- [x] The connection state is still available in words on an `aria-live` region
- [x] Every existing gesture survives: pan, zoom, drag, resize, pile, unpile, double-click enlarge, drag-to-trash, drag out of the inbox
- [x] No build step, no npm, no binary asset — the grain is generated into a data URL at boot
