# 15: Two desks — day and night, plants, and a real cup

**What to build:** The literal desk from ticket 14 gains a material. Two of
them: `day`, an oak table in a coffee shop on a warm terrazzo floor, and
`night`, dark walnut under a single warm lamp. Plants stand at the corners. The
coffee — which is still the connection indicator — becomes a real photograph,
because drawn gradients cannot make a convincing crema at the size it is drawn.

**Blocked by:** 14

**Status:** done

The seam is the point here. `desk.css` owns structure and holds no colour of
its own; `skins.css` holds nothing but colour, light, and which props are on.
That is what makes a skin unable to move a sheet or change a gesture, and it is
why adding the second skin cost no structural change at all.

A third skin, `premium`, was built and then cut: figured walnut with a heavier
lamp and two plants. It was not good enough to keep, and three desks is two
more than anyone needs to choose between.

- [x] `desk.css` expresses every material as a custom property and hardcodes no colour, so a skin is a change of variables and nothing else
- [x] Switching skins cannot move a sheet, alter a gesture, or change the desk's geometry
- [x] `day` puts the desk in a lit room: oak, warm terrazzo floor, light chrome — no black anywhere
- [x] `night` lights the desk with one lamp that is a real object at a fixed spot on the slab, so panning moves the pool with the wood
- [x] No skin paints over `.sheet-body`; a figure is never tinted, dimmed, or colour-cast by the room it sits in
- [x] The skin persists per browser in `localStorage`, is overridable with `?skin=`, and never reaches the server
- [x] The skin is chosen before first paint, so the desk never flashes the wrong material
- [x] One grain plate serves both woods: it is generated as an alpha mask and each skin tints and scales it
- [x] Plants are decoration only — no hit area, no state, no behaviour — and stand clear of the inbox
- [x] The coffee is a real photograph, masked to the cup's rim circle, with the ceramic rim and handle drawn around it so cup and handle are one material
- [x] Each skin grades the same cup into its own light, and the same mechanism is what makes it go cold
- [x] The photograph is public domain and `web/ASSETS.md` records its source, licence, and exact processing
