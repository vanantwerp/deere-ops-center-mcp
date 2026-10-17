# Changelog

## Unreleased

- `login` now suits switching John Deere accounts: John Deere asks for your password every time, the organization page opens only for organizations you can connect, it lists which organizations are connected when it finishes (and says to sign out at connections.deere.com if one is not), and it stops waiting after three minutes if you close that page.
- A running server now notices a new sign-in. After `logout` and `login` in a terminal, the next request uses the new sign-in, so Claude Desktop no longer needs a restart to switch John Deere users.
- `login` renews the sign-in once more after you connect the application to an organization. Before, the sign-in issued moments earlier did not see the new connection, and fields and boundaries answered "403 Forbidden" until the next `login`.
- The browser sign-in now also uses PKCE, an extra check that John Deere's sign-in service supports: a one-time secret made for each sign-in stays on this computer, so a sign-in code picked up on its way back from the browser is no use to anyone else. The Application Secret is still used as before.

## 0.1.0 - 2026-10-16

First release. Everything below is new.

- Runs on your own computer as an MCP server over stdio. Works with Claude Desktop, Claude Code and other MCP-capable apps.
- Read-only. The sign-in asks John Deere only for read permissions, and no tool creates, edits or deletes anything in Operations Center.
- Command line: `setup` (saves your Application ID and Secret, signs in, then offers to connect your AI apps), `connect`, `login`, `logout` and `config`.
- `setup` writes the entry for this server into Claude Desktop's settings file itself, using the full location of `uvx`, so you do not look up that location or edit the file by hand. Other servers already listed in the file are kept, and a backup copy is saved before the file is changed. If Claude Code is installed, `setup` also offers to add the server there. The new `connect` command does only this step, without signing in, so you can run it again after reinstalling `uv`. If setup does not find Claude Desktop, the README's "Manual configuration" section has the steps to do it by hand.
- `config` also shows where Claude Desktop's settings file is, whether it has the `deere` entry, and whether Claude Code was found.
- Sign-in tokens renew themselves and are kept in a folder only your user account can read. `logout` removes them and tells John Deere to cancel them.
- Organizations, with a flag for organizations that still need to be connected to your application.
- Fields and field boundaries.
- Field operations (tillage, planting, applications, harvests) with crop, products, varieties and measurement totals such as area, seeding rate and yield, plus per-operation measurement details.
- Guidance lines, products, operators and work plans.
- Equipment, and machine health: alerts, engine hours, hours of operation, location history and device state reports.
- A general read-only `jd_get` tool for John Deere endpoints that have no tool of their own. It only talks to Deere hosts, and it can return measurement map images.
- File tools: download an operation's shapefile as a zip, save text such as a CSV, and export several seasons of a field's operations to one JSON file. Files are only written inside your output folder, and these three tools are marked as writing tools, so an AI app that asks before a tool changes anything asks for them; every other tool is marked read-only.
- Long lists are fetched page by page, up to a page cap (default 20) that you can change. The tool says so when a list was cut short.
- Optional settings for measurement system, yield units, sign-in port, output folder and John Deere environment. See `.env.example`.
- Tests that run without any John Deere account, and a GitHub Actions check on Linux, macOS and Windows.
