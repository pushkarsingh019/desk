# Third-party assets

The desk is otherwise drawn entirely in CSS and inline SVG. This directory
holds the exceptions, and this file is why they are allowed to be here.

## `latte.png`

A cup of coffee photographed from directly above, used as the desk's coffee —
which is also the desk's connection indicator. Drawn CSS gradients could not
make a convincing crema or latte art at this size, so this is a real photograph.

- **Source:** [Cappuchino latte art.jpg](https://commons.wikimedia.org/wiki/File:Cappuchino_latte_art.jpg),
  Wikimedia Commons
- **Author:** Blanka Novotná
- **Licence:** Public domain — no attribution required and no share-alike
  obligation, which is why this file was chosen over several better-composed
  CC BY-SA candidates. The credit above is courtesy, not a condition.
- **Processing:** the blue table it was shot on was removed by masking to the
  cup's rim circle, fitted radially at centre (443, 490) radius 361 in the
  960px source. Downscaled to 320px and quantised to 192 colours with alpha,
  which is 31 KB against 172 KB for the same image as full-colour PNG and is
  indistinguishable at the size it is drawn.

The photograph's own handle is clipped by the edge of the source frame, so it
was discarded; the handle on the desk is drawn in CSS and tinted to the rim
tones sampled from this file.
