/**
 * The desk's ear for the feedback button, for pi.
 *
 * pi has no background commands that wake the model, so this extension holds
 * `desk feedback --wait` open for the whole session instead: the same scope a
 * bare `desk feedback` has, the directory pi is working in. When the user
 * presses **feedback** on a sheet made from here, the report arrives as a
 * message and starts a turn, exactly as if the user had typed `/desk
 * feedback <path>`. When nobody asks, the wait ends quietly and is started
 * again. Nothing here writes, resolves, or removes a comment.
 *
 * Linked into ~/.pi/agent/extensions by scripts/install.sh. pi loads .ts as
 * it is; there is no build step.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const NOBODY_ASKED = /nobody asked for feedback/;

function deskCommand(): string {
  if (process.env.DESK_COMMAND) return process.env.DESK_COMMAND;
  const local = join(homedir(), ".local", "bin", "desk");
  return existsSync(local) ? local : "desk";
}

export default function (pi: ExtensionAPI) {
  let child: ChildProcess | null = null;
  let stopped = false;

  const listen = (cwd: string) => {
    if (stopped) return;
    // A desk that is not running answers at once, nonzero; wait before
    // asking again rather than spinning on a desk that is down.
    child = spawn(deskCommand(), ["feedback", "--wait"], { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    child.stdout?.on("data", (chunk) => { out += chunk; });
    child.stderr?.on("data", () => {});
    child.on("exit", (code) => {
      child = null;
      if (stopped) return;
      const report = out.trim();
      if (code === 0 && report && !NOBODY_ASKED.test(report)) {
        pi.sendMessage(
          {
            customType: "desk-feedback",
            content:
              "The user pressed feedback on the desk. This is `desk feedback` for that sheet; " +
              "act on it as the desk skill says.\n\n" + report,
            display: true,
          },
          { deliverAs: "followUp", triggerTurn: true },
        );
      }
      setTimeout(() => listen(cwd), code === 0 ? 0 : 30_000);
    });
  };

  pi.on("session_start", (_event, ctx) => {
    stopped = false;
    listen(ctx.cwd);
  });

  pi.on("session_shutdown", () => {
    stopped = true;
    if (child) {
      child.kill();
      child = null;
    }
  });
}
