# 19: Feedback for the agent

**What to build:** The agent reads the user's comments back. `desk feedback
[path] [--json]` prints every open comment on the current desk, or on one
sheet, with where each one is in terms the agent can act on: fractions, pixels
against the current version, and a coarse verbal location. `desk present` says
when the sheet it just presented has open comments, and the `/desk` skill
tells the agent what to do about that: read the *feedback*, change the plotting
code, re-run so the watched sheet updates in place, and tell the user to look
and resolve what is fixed.

**Blocked by:** 18

**Status:** done

This is the second half of the "Annotation and two-way feedback" reversal, and
the part that was called the strongest reason this beats `scp`. What stays cut:
read-back, since the agent never learns whether the user looked and feedback
reaches it only when the user types a command; model-invoked anything; and
agent-authored text, since the agent reads comments and never writes, resolves,
removes, or replies to one. The loop is the user's to drive — look at the
figure, pin comments in the browser, type `/desk`, and the agent reads and
fixes.

- [x] `desk feedback` with no path reports the live sheets with open comments whose files are under the directory the agent is working in (the same scope as bare `present`), newest first, so that on a full desk the agent reads the comments on what it just put up; `--all` is every sheet on the current desk; with a path, that sheet, resolved the way `present` resolves one; trashed sheets, sent sheets outside `--all`, and resolved comments are skipped
- [x] Per sheet: the source path, its origin if it has one, and the current version; per open comment: its number, the version it was made on, the region as fractions and as pixels against the current version (and against the commented version when that is still retained), a coarse verbal location from a three-by-three grid of the box ("upper right", "left edge, middle", "whole sheet"), and the text
- [x] Pixels come from a natural size the server computes from the stored bytes at report time (PNG from the IHDR chunk, SVG from `width`/`height` or `viewBox`, no dependencies) and reports in the sheet JSON; the `desk` command measures nothing itself. When the size cannot be read, the report gives fractions, says why, and still exits 0
- [x] A comment whose version has been evicted is reported as "on vN, no longer retained", with pixels against the current version only
- [x] `--json` prints the same report as one object: a list of sheets, each with `source_path`, `origin`, `version`, `natural_size`, and `comments`, each comment with `id`, `version`, `anchor`, `pixels`, `location`, and `text`
- [x] With nothing open it prints `no open feedback` and exits 0; with a path that is not a sheet on the current desk it exits nonzero and says so; when the desk cannot be reached it exits nonzero and never starts the server, unlike `present`
- [x] `desk present` prints a third line when the sheet it presented has open comments: `2 open comments on this sheet — run: desk feedback <path>`; `desk present --to` never does, because comments never travel back to the sender
- [x] The `/desk` skill gets a second branch: `/desk feedback [path]` runs `desk feedback` and the agent acts on what it prints
- [x] The skill says that after any `desk present` whose output mentions open comments, the agent runs `desk feedback <that path>` and acts on it before doing anything else
- [x] The skill says what acting means: restate each open comment as one concrete change to the plotting code, make the change, re-run the script so the watched sheet updates in place, then tell the user to look at the desk and resolve what is fixed
- [x] The skill says a sheet with an origin is a copy whose script is not on this machine, so its feedback is to be reported, not acted on, and the agent says so rather than guessing
- [x] The skill says the agent never resolves, removes, edits, or writes a comment; it stays `disable-model-invocation: true`; and it describes the user's loop in full: look at the figure, pin comments in the browser, type `/desk` or `/desk feedback`, the agent reads and fixes, the user resolves
- [x] `CONTEXT.md` gets the noun **Feedback**, and load-bearing rule 2 gets one more sentence: the agent may read feedback but still cannot put text on the desk; `SPEC.md` amends (not deletes) the "Read-back / acknowledgement" cut, adds a user story, and adds an Implementation Decisions paragraph; `README.md` and `SETUP.md` mention `desk feedback` where they list commands
- [x] Tested through the `desk` command in `tests/test_api.py`, invoked as the existing `desk present` tests invoke it: the report text and `--json`; pixel conversion for a PNG and an SVG; "no longer retained"; "no open feedback"; the nonzero exits; and `present`'s extra line, present and absent
