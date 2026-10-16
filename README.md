# Deere Ops Center MCP

> Draft. Written before the code exists, against the agreed design. Commands and paths below are what the finished tool will use. The repository lives at github.com/vanantwerp/deere-ops-center-mcp.

Deere Ops Center MCP lets an AI assistant such as Claude read your John Deere Operations Center data and answer questions about it. It runs on your own computer. It can only read. It cannot change anything in Operations Center.

With it connected, you can ask things like:

- "List my fields and tell me which ones are archived."
- "What was planted on the North 80 in 2023, and what was the yield?"
- "Show every application on field 12 for the last five seasons."
- "Which machines have active alerts right now?"
- "What are this year's work plans for the home farm?"

This is a community project. It is not made, supported or endorsed by Deere & Company.

---

## What you need

1. **A John Deere Operations Center account** with access to at least one organization.
2. **A free John Deere developer application.** This gives you two codes that let the tool sign in to Operations Center on your behalf. Creating one takes about five minutes. Step 1 below explains how.
3. **A computer** running Windows, macOS or Linux.
4. **An AI app that supports MCP.** Claude Desktop and Claude Code both do. Other MCP-capable apps work too.
5. **A small free helper program called `uv`.** It downloads and runs the tool for you. Installing it is one command, shown in each quick start.

---

## Step 1: Create your John Deere application

This is the same on every platform. You do it once.

1. Go to [developer.deere.com](https://developer.deere.com) and sign in with your John Deere account.
2. Open **My Applications** and choose **Create Application**. Give it any name, for example "My AI assistant".
3. Find the **Redirect URIs** setting and add this exact address:

   ```
   http://localhost:9090/callback
   ```

   It has to match character for character. This address only works on your own computer; it is how John Deere hands the sign-in back to the tool.

4. Save. The page now shows an **Application ID** and a **Secret**. Keep both handy for Step 2. Treat the Secret like a password: do not paste it into chats, emails or shared documents.

New applications start in John Deere's "sandbox" mode. Sandbox is real data from your real organizations, with two limits: at most 5 connected organizations, and 150,000 requests per month. That is more than enough for personal use.

---

## Step 2: Quick start on macOS

1. **Install `uv`.** Open the Terminal app and paste:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Close Terminal and open it again so it knows about the new program. (If you already use Homebrew, `brew install uv` works too.)

2. **Run the setup.** Paste this into Terminal:

   ```bash
   uvx --from git+https://github.com/vanantwerp/deere-ops-center-mcp deere-mcp setup
   ```

   It asks for your Application ID and Secret, saves them on your computer, and opens your web browser so you can sign in to John Deere. The first time, John Deere also asks which of your organizations the application may see. Choose them and confirm. When Terminal says `Signed in`, you are done with this step.

3. **Find where `uvx` lives.** Claude Desktop needs the full location. In Terminal:

   ```bash
   which uvx
   ```

   It prints something like `/Users/yourname/.local/bin/uvx`. Copy that.

4. **Connect Claude Desktop.** Open Claude Desktop, go to **Settings → Developer → Edit Config**. A file called `claude_desktop_config.json` opens. Make it look like this, using the location from the previous step:

   ```json
   {
     "mcpServers": {
       "deere": {
         "command": "/Users/yourname/.local/bin/uvx",
         "args": ["--from", "git+https://github.com/vanantwerp/deere-ops-center-mcp", "deere-mcp"]
       }
     }
   }
   ```

   Save the file and quit and reopen Claude Desktop.

5. **Try it.** Ask Claude: "List my John Deere organizations."

---

## Step 2: Quick start on Windows

1. **Install `uv`.** Open PowerShell (search for it in the Start menu) and paste:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   Close PowerShell and open it again. (If you prefer, `winget install --id=astral-sh.uv -e` does the same.)

2. **Run the setup.** Paste this into PowerShell:

   ```powershell
   uvx --from git+https://github.com/vanantwerp/deere-ops-center-mcp deere-mcp setup
   ```

   It asks for your Application ID and Secret, saves them on your computer, and opens your web browser so you can sign in to John Deere. The first time, John Deere also asks which of your organizations the application may see. Choose them and confirm. When PowerShell says `Signed in`, you are done with this step.

3. **Find where `uvx` lives.** Claude Desktop needs the full location. In PowerShell:

   ```powershell
   where.exe uvx
   ```

   It prints something like `C:\Users\yourname\.local\bin\uvx.exe`. Copy that.

4. **Connect Claude Desktop.** Open Claude Desktop, go to **Settings → Developer → Edit Config**. A file called `claude_desktop_config.json` opens. Make it look like this, using the location from the previous step. In this file every backslash in a path is written twice:

   ```json
   {
     "mcpServers": {
       "deere": {
         "command": "C:\\Users\\yourname\\.local\\bin\\uvx.exe",
         "args": ["--from", "git+https://github.com/vanantwerp/deere-ops-center-mcp", "deere-mcp"],
         "env": { "PYTHONUTF8": "1" }
       }
     }
   }
   ```

   Save the file and quit and reopen Claude Desktop.

5. **Try it.** Ask Claude: "List my John Deere organizations."

---

## Step 2: Quick start on Linux

1. **Install `uv`.** In a terminal:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Open a new terminal afterwards.

2. **Run the setup.**

   ```bash
   uvx --from git+https://github.com/vanantwerp/deere-ops-center-mcp deere-mcp setup
   ```

   Follow the prompts and sign in to John Deere in the browser that opens. If no browser opens, copy the link the terminal prints into one.

3. **Connect your AI app.** Claude Desktop is not available on Linux. Use Claude Code (next section) or any other MCP-capable app, giving it the command `uvx` with the arguments `--from git+https://github.com/vanantwerp/deere-ops-center-mcp deere-mcp`.

---

## Using it with Claude Code (any platform)

After Step 1 and the `setup` command for your platform, run this once in a terminal:

```bash
claude mcp add deere -- uvx --from git+https://github.com/vanantwerp/deere-ops-center-mcp deere-mcp
```

Then start Claude Code and ask: "List my John Deere organizations."

---

## Everyday use

**Asking questions.** Just ask in plain words. The assistant picks the right tool. Field names, organization names and years all work.

**Signing in again.** Your sign-in normally renews itself. If the assistant reports that the session expired, run the setup command for your platform again, or just:

```bash
uvx --from git+https://github.com/vanantwerp/deere-ops-center-mcp deere-mcp login
```

**Signing out.** This removes the saved sign-in from your computer and tells John Deere to cancel it:

```bash
uvx --from git+https://github.com/vanantwerp/deere-ops-center-mcp deere-mcp logout
```

**Updating to the newest version.** Add `--refresh` once:

```bash
uvx --refresh --from git+https://github.com/vanantwerp/deere-ops-center-mcp deere-mcp login
```

**Where your sign-in is kept.** In a folder only your user account can read:

| Platform | Folder |
|---|---|
| macOS | `~/Library/Application Support/deere-mcp` |
| Windows | `%LOCALAPPDATA%\deere-mcp` (usually `C:\Users\yourname\AppData\Local\deere-mcp`) |
| Linux | `~/.config/deere-mcp` |

It holds your Application ID, Secret and sign-in tokens. Do not copy it to other machines or share it. To see the exact folders on your computer, run:

```bash
uvx --from git+https://github.com/vanantwerp/deere-ops-center-mcp deere-mcp config
```

---

## Settings you can change (optional)

Everything works without touching these. Set them as environment variables, or add them to the `config.toml` file in the folder above. An environment variable wins if both are set.

| Setting | What it does | Default |
|---|---|---|
| `DEERE_UOM` | Measurement system: `ENGLISH` (acres, bushels) or `METRIC` (hectares, tonnes). | Not set, so each organization's own preference in Operations Center is used. |
| `DEERE_YIELD` | How yield is expressed: `VOLUME` (bushels) or `MASS` (pounds, tonnes). | Not set, organization preference. |
| `DEERE_CALLBACK_PORT` | The port used during sign-in. Change it only if 9090 is already in use on your computer, and change the Redirect URI in your John Deere application to match. | `9090` |
| `DEERE_PAGE_CAP` | The most pages of results fetched for one question before the tool stops and says the list was cut short. | `20` |
| `DEERE_OUTPUT_DIR` | The only folder the tool is allowed to write files into (downloads and exports). | A `deere-mcp` folder in your Documents. |
| `DEERE_BASE_URL` | Which John Deere environment to talk to. | The sandbox: `https://sandboxapi.deere.com/platform`. If John Deere later approves your application for production, change this to the production address they give you. |
| `DEERE_CLIENT_ID`, `DEERE_CLIENT_SECRET` | Your Application ID and Secret. The `setup` command stores these for you; set them here only if you prefer not to use the stored file. | None |

---

## What it can and cannot do

**It can read:**

- Organizations, clients, farms and fields, including field boundaries
- Field operations: tillage, planting, applications and harvests, with dates, products, rates, varieties, crop and measurement totals such as area, seeding rate and yield
- Equipment, including machine alerts, engine hours, hours of operation, location history and terminal status
- Work plans, operators, products and guidance lines
- One per-operation map image per measurement, for a visual sense of where values were high or low

**It cannot:**

- Create, edit or delete anything. The sign-in only asks John Deere for read permissions, so even a mistake cannot write.
- Show seasons from before your farm started using Operations Center. The data has to be there first.
- Hand the assistant point-by-point yield maps. Those are large files; the tool can download them to your output folder for use in other software.

**The permissions it asks John Deere for**, in plain words: view fields and production data, view equipment, view operators, view work plans, and stay signed in between uses.

---

## Troubleshooting

**"redirect_uri mismatch" or "invalid redirect" during sign-in.** The Redirect URI in your John Deere application is not exactly `http://localhost:9090/callback`. Fix it there and run `login` again.

**The organization list is empty, or you get "403 Forbidden".** The application has not been connected to your organization yet. Run `login` again and complete the organization-selection page John Deere shows, or visit connections.deere.com and connect it there.

**Claude Desktop says the server could not start.** Almost always the `command` path. Run `which uvx` (macOS) or `where.exe uvx` (Windows) and use the exact result. On Windows, also confirm the `"env": { "PYTHONUTF8": "1" }` line is present. Claude Desktop's own logs are under `~/Library/Logs/Claude` on macOS and `%APPDATA%\Claude\logs` on Windows.

**No browser opened during sign-in.** Copy the link printed in the terminal into any browser.

**"Session expired."** Run `login` again.

**A question returned "list was cut short".** The result had more than the page cap allows. Narrow the question (a single season, a shorter date range) or raise `DEERE_PAGE_CAP`.

**Machine alerts or locations fail with a date error.** John Deere limits alerts to 7 days per request and location history to 1 month per request. Ask for a shorter window.

---

## Privacy

Your data moves between your computer and John Deere only. There is no third-party server. The AI app you connect sees what the tools return in answer to your questions, under that app's own privacy terms. Nothing is logged except error messages, and sign-in tokens are never written to logs.

---

## For developers

```bash
git clone https://github.com/vanantwerp/deere-ops-center-mcp
cd deere-ops-center-mcp
uv sync
uv run pytest
uv run deere-mcp setup
```

Each tool is one function in `src/deere_mcp/tools/`, with a docstring that becomes the description the assistant sees. See `CONTRIBUTING.md` for how to add one and `SECURITY.md` for how to report a problem.

## Acknowledgements

This project was inspired by [john-deere-ops-center-mcp](https://github.com/CoreyFransen08/john-deere-ops-center-mcp) by Corey Fransen, a John Deere MCP server built for Cloudflare Workers, which served as a reference for the Operations Center endpoints and parameters. Deere Ops Center MCP is an independent rewrite in Python and shares no code with it.

## License

MIT. See `LICENSE`.
