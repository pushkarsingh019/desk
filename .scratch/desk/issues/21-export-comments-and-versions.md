# 21: Export a sheet's comments and versions

**What to build:** `desk export [path] [--all] [--out DIR]` writes a
self-contained directory that another agent can open without the desk
installed: every comment on each sheet in scope, open and resolved, with the
version it was made on and the version it was resolved on, and the retained
version files byte for byte. A `manifest.json` describes it all and a short
`README.md` in the bundle says how to read it. This is the record of what
the user said about a figure and what the figure looked like before and
after, which is what someone needs to hill-climb on the critique with a
different agent, or to distil a skill from it.

**Blocked by:** 19

**Status:** done

Export is a read, like `feedback`: it goes through the HTTP API, never
starts the server, and never writes to the desk. It is the user's command,
not part of the agent's loop, so the `/desk` skill does not change. What
stays cut: agent-authored text (the bundle is the user's comments and the
user's figures, plus a format note that names no figure), any channel back
into the desk, and the version-stepping UI. The one rule this changes is
retention: a version a comment names — the one it was made on, or the one it
was resolved on — is kept past the cap of 20, because the cap exists to
bound disk and a commented version is bounded by the user's hand. Without
that, a hill-climbing session evicts the "before" picture long before anyone
exports it.

- [x] `resolve` stamps the sheet's current version on the comment as `resolved_version`, the way `add` stamps `version`; a comment resolved before this field existed reads back with `resolved_version: null`
- [x] The last 20 versions are retained, plus any older one a comment names in `version` or `resolved_version`; removing a comment releases what only it named, at once
- [x] Sheet JSON carries `history`: one record per retained version with `n`, `sha256`, `bytes`, `created_at`, and `content_url`; `versions` keeps its shape, a list of numbers
- [x] `desk export` has the scope of `desk feedback`: sheets under the working directory by default, `--all` for the current desk, a path for one sheet; live sheets with at least one comment, open or resolved, newest first; sent sheets only with `--all` or by path
- [x] `--out DIR` is where the bundle goes (default `desk-export-<YYYYMMDD-HHMMSS>` in the working directory); an existing non-empty directory is refused with a nonzero exit
- [x] The bundle is `manifest.json`, `README.md`, and `sheets/<NN>-<stem>/v<n>.<kind>` for every retained version, byte-identical to what was published
- [x] `manifest.json` has `schema: 1`, `desk`, `exported_at`, `under` when scoped, and per sheet: `source_path`, `origin`, `kind`, `name`, `dir`, `natural_size`, `versions` (`n`, `sha256`, `bytes`, `created_at`, `retained`, `file` relative to the bundle or null), and `comments` (`id`, `number`, `version`, `resolved_version`, `anchor`, `location`, `pixels_on_version`, `text`, `created_at`, `resolved_at`)
- [x] An evicted version a comment still names appears in `versions` with `retained: false` and `file: null`, so the manifest never claims a file it does not have
- [x] With nothing to export it prints `nothing to export` and exits 0; a path that is not a sheet exits nonzero and says so; when the desk cannot be reached it exits nonzero and never starts the server
- [x] `CONTEXT.md` amends the **Version** noun and the **Resolve** verb and adds the user-only verb **Export**; `SPEC.md` gets story 47, an Implementation Decisions paragraph, and the retention sentence in the Store module; `README.md` and `SETUP.md` list the command where they list `desk feedback`
- [x] Tested through the `desk` command in `tests/test_api.py`, as `feedback` is: the manifest shape; a version file byte-identical to the published bytes; a resolved comment with its `resolved_version`, and one resolved before the field existed reading back null; a commented version surviving the 21st publish and an uncommented one evicted (`retained: false`); scope; `nothing to export`; the nonzero exits
