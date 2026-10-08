# 16: Someone else's desk

**What to build:** One person puts a figure on another person's desk. `/desk
fig.svg --to <their machine>` sends the file's bytes to the desk running there;
it lands in *their* inbox, marked with where it came from. Sending the same
file again updates that sheet in place. Nothing else about their desk changes:
where the sheet goes is still theirs to decide.

**Blocked by:** 13

**Status:** done

This is the cut called "multi-machine publishing" coming back in a narrower
form, at the user's request. What stays cut is cross-machine *watching*: a desk
watches files on its own disk only, so a sent sheet is a copy that updates when
it is sent again and not before. A relay — the sender's desk forwarding every
new version — was considered and rejected: it needs a registry of remote
subscriptions, fails silently in the background, and would resurrect a sheet
the recipient had thrown away every time the sender's script re-ran, which is
the zombie the trash exists to prevent.

- [x] `desk present <path> --to <machine[:port]>` sends the file's bytes to that machine's desk, and never starts or publishes to a local one
- [x] A sent sheet lands in the recipient's inbox with an `origin` naming the machine it came from, and the page shows it
- [x] Sheet identity is the origin plus the sender's absolute source path: the same file sent again is a new version on the same sheet, position and size untouched; the same path from two machines is two sheets; a local sheet at that path is a third
- [x] A sent sheet is never watched, even when a file exists at the same path on the recipient's machine, and stays unwatched across a restart
- [x] Sending is explicit: a sent sheet the recipient threw away comes back to the inbox when it is sent again
- [x] The recipient's desk refuses a send with no origin, a malformed origin, content that is not base64, or a file type the desk does not take — with a clear error and no sheet created
- [x] `desk present --to` exits nonzero and says which desk it could not reach, so a figure that did not arrive is never reported as one that did
- [x] The origin a machine sends under is the same on every send from that machine
- [x] The skill tells the agent how to send and that a sent sheet is a copy; `CONTEXT.md`, `SPEC.md`, `README.md`, and `SETUP.md` say the same
- [x] All behaviour is tested through the HTTP API seam and the `desk` command
