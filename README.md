# Desk

A visuo-spatial sketchpad for agent-produced figures.

You run a coding agent over SSH on `pushkar-studio`. It makes a plot. You type
`/desk`. The figure appears on a web page you keep open on another machine,
where you put it, at the size you made it. When the agent re-runs the script,
the sheet updates in place — same position, same size — so you can keep staring
at one spot while it iterates.

Served over Tailscale at `http://pushkar-studio.taila96c04.ts.net:7777`.

## Status

Not built yet. This repo contains the spec and the ticket breakdown, ready for
implementation.

- `SPEC.md` — the full specification: problem, solution, 40 user stories,
  implementation and testing decisions, and what was deliberately left out.
- `CONTEXT.md` — domain glossary. Use this vocabulary in code and commits.
- `CLAUDE.md` — instructions for the agent building this.
- `.scratch/desk/issues/` — thirteen tickets in dependency order.

## How to build it

On `pushkar-studio` (the server must run on the same machine as the agent —
watching is a filesystem operation):

```
git clone <this repo> ~/code/desk
cd ~/code/desk
```

Then work the frontier — any ticket whose blockers are all done — clearing
context between each:

```
/mattpocock-skills:implement .scratch/desk/issues/01-walking-skeleton.md
```

Ticket 01 is the only one that can start immediately. Finishing it unblocks
02, 05, 11 and 12 at once.

## Optional detour before tickets 05–09

Drag, pile, and pan *feel* cannot be settled on paper. A throwaway canvas
prototype (`/mattpocock-skills:prototype`) would answer whether the desk
metaphor works under your hand before the layout model gets locked into tests.
Piles are the part most likely to disappoint: "drag onto, click to fan" sounds
right and may feel fiddly at speed.
