# 12: Deployment — tailnet binding, launchd, port

**What to build:** The desk is simply always there. The user opens a URL from
another machine on the tailnet and it works, including after a reboot, with
nothing to start by hand.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] The server binds to the Tailscale interface and is reachable from another tailnet device at `http://pushkar-studio.taila96c04.ts.net:<port>`
- [ ] The server is not reachable from outside the tailnet
- [ ] A launchd user agent starts the server at login and restarts it if it dies
- [ ] The chosen port does not collide with the launchd agents already on studio
- [ ] Installation is one documented command
- [ ] Server logs go somewhere findable and are documented
- [ ] The desk's contents survive a full reboot
