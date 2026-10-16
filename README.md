# Deere Ops Center MCP

Deere Ops Center MCP lets an AI assistant such as Claude read your John Deere Operations Center data: fields, field operations, equipment, work plans and more. It runs on your own computer. It can only read; it cannot change anything in Operations Center.

---

## What you need

1. **A John Deere Operations Center account** with access to at least one organization.
2. **A free John Deere developer application.** It gives you two codes that let the tool sign in on your behalf. Step 1 explains how.
3. **A computer** running Windows, macOS or Linux.
4. **An AI app that supports MCP**, the standard AI apps use to connect to tools like this one. Claude Desktop (the Claude app for Windows and macOS) and Claude Code (Claude's command-line tool) both do.
5. **`uv`**, a free helper program that downloads and runs this tool. Installing it is one command, shown in each quick start. `uvx` is the part of `uv` that runs a tool.

---

## Step 1: Create your John Deere application

The same on every platform. You do it once.

1. Sign in at [developer.deere.com](https://developer.deere.com) with your John Deere account.
2. Create a new application (under **My Applications**) and give it any name.
3. Find the application's redirect URI setting and add this exact address:

   ```
   http://localhost:9090/callback
   ```

   It must match character for character. It only works on your own computer; it is how John Deere hands the sign-in back to the tool.

4. Save.
5. Give the application access to the APIs. Open the application (**View Details**), go to its **Access** tab and choose **Request Access**, then select the Operations Center APIs: organizations, fields, field operations, equipment and machine data, work plans, operators, products and guidance lines. Some are granted at once; Deere says others can take up to a day. Until this is done, every request is refused with "403 Forbidden".
6. The application's page shows an **Application ID** and a **Secret**. Keep both for Step 2. Treat the Secret like a password.

New applications start in John Deere's sandbox, which works with your real organizations within limits Deere sets.

---

## Step 2: Quick start on macOS

1. **Install `uv`.** Open Terminal (press Command+Space, type `Terminal`, press Return). Paste this line and press Return:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Close Terminal and open it again. If you use Homebrew, `brew install uv` also works.

2. **Run setup.** Paste this and press Return:

   ```bash
   uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp setup
   ```

   The first run downloads the tool. Setup then:

   - asks for your Application ID, then your Secret. Paste each and press Return. The Secret does not show as you paste it.
   - opens your browser to sign in to John Deere. The first time, John Deere also asks which of your organizations the application may see. Choose them and confirm.
   - asks `Add the server to Claude Desktop? [Y/n]`. Press Return for yes.
   - if Claude Code is installed, asks `Also add it to Claude Code? [Y/n]`. Press Return for yes, or type `n` and Return.

   It ends with `Signed in. Quit and reopen Claude Desktop to finish.` If it ends with only `Signed in.`, it did not find Claude Desktop: see [Manual configuration](#manual-configuration), then continue with step 4.

3. **Quit and reopen Claude Desktop.** Closing the window is not enough. In the menu bar choose **Claude**, then **Quit Claude** (Command+Q), and open it again.

4. **Try it.** Ask Claude: "List my John Deere organizations."

---

## Step 2: Quick start on Windows

1. **Install `uv`.** Open PowerShell (click Start, type `PowerShell`, choose **Windows PowerShell**). Paste this line and press Enter. To paste, press Ctrl+V or right-click in the window.

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   Close PowerShell and open it again. `winget install --id=astral-sh.uv -e` does the same if you prefer.

2. **Run setup.** Paste this and press Enter:

   ```powershell
   uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp setup
   ```

   The first run downloads the tool. Setup then:

   - asks for your Application ID, then your Secret. Paste each and press Enter. The Secret does not show as you paste it.
   - opens your browser to sign in to John Deere. The first time, John Deere also asks which of your organizations the application may see. Choose them and confirm.
   - asks `Add the server to Claude Desktop? [Y/n]`. Press Enter for yes.
   - if Claude Code is installed, asks `Also add it to Claude Code? [Y/n]`. Press Enter for yes, or type `n` and Enter.

   It ends with `Signed in. Quit and reopen Claude Desktop to finish.` If it ends with only `Signed in.`, it did not find Claude Desktop: see [Manual configuration](#manual-configuration), then continue with step 4.

3. **Quit and reopen Claude Desktop.** Closing the window is not enough. Right-click the Claude icon at the right end of the taskbar (click the small up arrow there if it is hidden) and choose **Quit**. If there is no icon, the app has already quit. Open Claude Desktop again from the Start menu.

4. **Try it.** Ask Claude: "List my John Deere organizations."

---

## Step 2: Quick start on Linux

1. **Install `uv`.** In a terminal, paste this line and press Enter:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Open a new terminal afterwards.

2. **Run setup.** Paste this and press Enter:

   ```bash
   uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp setup
   ```

   Setup asks for your Application ID and Secret (the Secret does not show as you paste it), opens your browser to sign in to John Deere and choose your organization, and, if Claude Code is installed, asks `Also add it to Claude Code? [Y/n]`. If no browser opens, copy the link the terminal prints into one. It ends with `Signed in.`

3. **Connect your AI app.** If setup found Claude Desktop or you answered yes for Claude Code, that app is connected. Otherwise use the command in the next section, or give any other MCP app the command `uvx` with the arguments `--from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp`.

---

## Using it with Claude Code (any platform)

`setup` offers to add the server to Claude Code when it finds it. If you skipped that question, or installed Claude Code later, run this once in a terminal:

```bash
claude mcp add --scope user deere -- uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp
```

---

## Installing with Claude

If you use the Code tab in Claude Desktop, paste this repository's address into it and ask Claude to install the server. Claude does everything except type your two codes.

1. Claude installs `uv`. You approve one command.
2. Claude opens the Terminal panel (a command window inside Claude Desktop) and starts setup there with `--yes`, which answers the two "add" questions for you:

   ```bash
   uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp setup --yes
   ```

3. You type the Application ID and Secret into that panel (the Secret is hidden), sign in to John Deere in the browser that opens, and choose your organization.
4. Claude checks the result with `deere-mcp config` and asks you to quit and reopen Claude Desktop.

Rules for Claude, or for anyone helping you:

- **Never ask for the Secret in chat, and never put it on a command line or in a settings file.** It belongs only in the hidden prompt.
- **Run `setup` and `login` only in the Terminal panel, where a person can type.** They wait for input and open a browser, so they cannot run as background commands.

Without a Terminal panel, follow the quick start for your platform.

---

## Everyday use

**Signing in again.** The sign-in renews itself. If the assistant says the session expired:

```bash
uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp login
```

**Signing out.** Removes the saved sign-in from your computer and cancels it at John Deere:

```bash
uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp logout
```

**Updating.** Add `--refresh` once:

```bash
uvx --refresh --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp login
```

**If you reinstall or move `uv`.** The Claude Desktop entry holds the full location of `uvx`. When that changes, Claude Desktop reports that the server could not start. Rewrite the entry without signing in again:

```bash
uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp connect
```

It asks the same questions as `setup` and prints one line per app: created, updated (with the name of the backup it saved first), unchanged, or skipped. A message ending in `Run "deere-mcp connect" to try again.` means the sign-in worked but the settings file was not changed; fix what it describes and run the command above.

**Where your sign-in is kept.** In a folder only your user account can read:

| Platform | Folder |
|---|---|
| macOS | `~/Library/Application Support/deere-mcp` |
| Windows | `%LOCALAPPDATA%\deere-mcp` (usually `C:\Users\yourname\AppData\Local\deere-mcp`) |
| Linux | `~/.config/deere-mcp` |

It holds your Application ID, Secret and sign-in tokens. Do not copy or share it. To see the exact locations, whether the Claude Desktop entry is present, and whether Claude Code was found:

```bash
uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp config
```

---

## Settings you can change (optional)

Everything works without these. Set them as environment variables, or in the `config.toml` file in the folder above. An environment variable wins if both are set.

| Setting | What it does | Default |
|---|---|---|
| `DEERE_UOM` | Measurement system: `ENGLISH` (acres, bushels) or `METRIC` (hectares, tonnes). | Not set: each organization's own preference in Operations Center is used. |
| `DEERE_YIELD` | How yield is expressed: `VOLUME` (bushels) or `MASS` (pounds, tonnes). | Not set: organization preference. |
| `DEERE_CALLBACK_PORT` | The port used during sign-in. Change it only if 9090 is in use, and change the Redirect URI in your John Deere application to match. | `9090` |
| `DEERE_PAGE_CAP` | The most pages of results fetched for one question before the tool stops and says the list was cut short. | `20` |
| `DEERE_OUTPUT_DIR` | The only folder the tool may write files into (downloads and exports). | A `deere-mcp` folder in your Documents. |
| `DEERE_BASE_URL` | Which John Deere environment to talk to. | The sandbox, `https://sandboxapi.deere.com/platform`. If John Deere approves your application for production, change this to the address they give you. |
| `DEERE_CLIENT_ID`, `DEERE_CLIENT_SECRET` | Your Application ID and Secret. `setup` stores these for you; set them here only if you prefer not to use the stored file. | None |

---

## What it can and cannot do

**It can read:**

- Organizations, clients, farms and fields, including field boundaries
- Field operations: tillage, planting, applications and harvests, with dates, products, rates, varieties, crop and measurement totals such as area, seeding rate and yield
- Equipment, with machine alerts, engine hours, hours of operation, location history and terminal status
- Work plans, operators, products and guidance lines
- One map image per measurement of an operation

**It cannot:**

- Create, edit or delete anything. The sign-in asks John Deere for read permissions only.
- Show seasons from before your farm started using Operations Center.
- Hand the assistant point-by-point yield maps. Those are large files; the tool can download them to your output folder for other software.

**The permissions it asks John Deere for:** view fields and production data, view equipment, view operators, view work plans, and stay signed in between uses.

---

## Troubleshooting

**"redirect_uri mismatch" or "invalid redirect" during sign-in.** The Redirect URI in your John Deere application is not exactly `http://localhost:9090/callback`. Fix it there and sign in again.

**"403 Forbidden" on everything, including listing organizations.** The application has not been given API access yet: see step 5 of Step 1. **The organization list works but data for an organization is refused.** The application is not connected to that organization yet. Sign in again and complete the organization page John Deere shows, or connect it at connections.deere.com.

**Claude Desktop says the server could not start.** Run `connect` (see "If you reinstall or move `uv`" above).

**"command not found: uv" (or uvx) after installing it.** Your terminal does not see the folder uv was installed in. Close and reopen the terminal first. If that does not help, run this once, then open a new terminal:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

On Windows, open a new PowerShell window; the installer updates the path for new windows only.

**No browser opened during sign-in.** Copy the link printed in the terminal into any browser.

**"Session expired."** Sign in again.

**"List was cut short."** The result had more pages than the page cap allows. Ask for less (one season, a shorter date range) or raise `DEERE_PAGE_CAP`.

**Machine alerts or locations fail with a date error.** John Deere allows at most 7 days of alerts and 1 month of location history per request. Ask for a shorter window.

---

## Manual configuration

Only needed if `setup` did not find Claude Desktop (it never asked `Add the server to Claude Desktop? [Y/n]` and ended with just `Signed in.`). These steps add the entry to Claude Desktop's settings file, `claude_desktop_config.json`, by hand. The file is JSON, where every quote, comma and bracket matters; copy the example exactly and change only the location from step 1.

### On macOS

1. **Find where `uvx` is.** Claude Desktop needs the full location. In Terminal:

   ```bash
   which uvx
   ```

   It prints something like `/Users/yourname/.local/bin/uvx`.

2. **Edit the settings file.** In Claude Desktop, open **Settings → Developer → Edit Config**. Make the file look like this, with the location from step 1:

   ```json
   {
     "mcpServers": {
       "deere": {
         "command": "/Users/yourname/.local/bin/uvx",
         "args": ["--from", "https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip", "deere-mcp"]
       }
     }
   }
   ```

   If the file already has an `mcpServers` section, add only the `"deere"` entry inside it, with a comma between entries. Save.

3. **Quit and reopen Claude Desktop**, as in step 3 of the macOS quick start, then continue with step 4 there.

### On Windows

1. **Find where `uvx` is.** Claude Desktop needs the full location. In PowerShell:

   ```powershell
   where.exe uvx
   ```

   It prints something like `C:\Users\yourname\.local\bin\uvx.exe`.

2. **Edit the settings file.** In Claude Desktop, open **Settings → Developer → Edit Config**. Make the file look like this, with the location from step 1. Every backslash is written twice:

   ```json
   {
     "mcpServers": {
       "deere": {
         "command": "C:\\Users\\yourname\\.local\\bin\\uvx.exe",
         "args": ["--from", "https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip", "deere-mcp"],
         "env": { "PYTHONUTF8": "1" }
       }
     }
   }
   ```

   Keep the `"env"` line; it makes the tool send text to Claude Desktop as UTF-8. If the file already has an `mcpServers` section, add only the `"deere"` entry inside it, with a comma between entries. Save.

3. **Quit and reopen Claude Desktop**, as in step 3 of the Windows quick start, then continue with step 4 there.

---

## Privacy

Your data moves between your computer and John Deere only. There is no third-party server. The AI app you connect sees what the tools return. Only error messages are logged; sign-in tokens never are.

---

## For developers

```bash
git clone https://github.com/vanantwerp/deere-ops-center-mcp
cd deere-ops-center-mcp
uv sync
uv run pytest
uv run deere-mcp setup
```

The tests use a fake John Deere, so they need no account. Before a pull request, run `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest -q`, the same checks CI runs.

Rules that keep the tool safe:

- Tools only read. The sign-in asks for read permissions and that stays true.
- No real Application IDs, Secrets, tokens or farm data anywhere in the repository. Use made-up values.
- No `print()` under `src/` except in `cli.py`; the server talks to the AI app over standard output.
- Never log tokens, secrets, or request or response bodies.

To add a tool, copy a small one such as `src/deere_mcp/tools/operators.py`. Its docstring is the description the assistant reads, so say what it returns, where its IDs come from, and any limit John Deere sets. Build addresses with `client.url(...)`, return a `dict`, raise `ToolError` for anything the assistant should read as a failure, and write files only through `resolve_output_path`. Add a test with the `mcp_client` fixture and note the change in `CHANGELOG.md`.

Found a security problem? Report it privately through the repository's **Security** tab on GitHub, not in a public issue, and leave real codes and tokens out of the report.

## Acknowledgements

Inspired by [john-deere-ops-mcp](https://github.com/CoreyFransen08/john-deere-ops-mcp) by Corey Fransen, a John Deere MCP server for Cloudflare Workers, which served as a reference for the Operations Center endpoints and parameters. This project is an independent rewrite in Python and shares no code with it.

## License

MIT. See `LICENSE`.
