# Deere Ops Center MCP

> Version 0.1.0. The `setup` command configures Claude Desktop for you, so you do not edit any settings file by hand. The repository lives at github.com/vanantwerp/deere-ops-center-mcp.

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
4. **An AI app that supports MCP.** MCP is a standard way for AI apps to connect to outside tools. This project is one such tool, called an "MCP server": the AI app starts it on your computer and asks it questions. Claude Desktop is the Claude app you install on Windows or macOS. Claude Code is Claude's command-line tool: you use it by typing commands in a terminal, the plain text window described in the quick starts below. Both support MCP, and other MCP-capable apps work too.
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

1. **Install `uv`.** Open the Terminal app: press Command+Space, type `Terminal` and press Return. Terminal is a window where you type or paste commands. Paste this line into it and press Return:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Close Terminal and open it again so it knows about the new program. (Homebrew is a program installer that some Mac users already have. If you use it, `brew install uv` works too.)

2. **Run the setup.** Paste this into Terminal and press Return:

   ```bash
   uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp setup
   ```

   `uvx` is the part of `uv` that downloads a tool and runs it. The first time, it can take a little while to download the tool. Then setup does these things, in this order:

   - It asks for your Application ID and then your Secret, the two codes from Step 1. Paste each one and press Return. The Secret does not show on the screen as you paste it; that is normal. Setup saves both on your computer.
   - It opens your web browser so you can sign in to John Deere. The first time, John Deere also asks which of your organizations the application may see. Choose them and confirm.
   - It asks `Add the server to Claude Desktop? [Y/n]`. "The server" is this tool, the small program that Claude Desktop starts to talk to John Deere. In `[Y/n]` the capital letter is the default answer, so pressing Return on its own means yes. Press Return.
   - If Claude Code is installed on your computer, it also asks `Also add it to Claude Code? [Y/n]`. Press Return for yes, or type `n` and press Return if you do not use Claude Code.

   When Terminal prints `Signed in. Quit and reopen Claude Desktop to finish.`, you are done with this step. Setup has written the entry that Claude Desktop needs, so there is no file for you to edit. If Claude Desktop already had other servers listed, they are kept, and setup saves a backup copy of the file first.

   If Terminal prints only `Signed in.`, setup did not find Claude Desktop. Make sure Claude Desktop is installed, then follow [Manual configuration](#manual-configuration) near the end of this page, which takes the place of step 3. After that, continue with step 4.

3. **Quit and reopen Claude Desktop.** Closing the window is not enough, because the app keeps running. In the menu bar at the top of the screen, choose **Claude**, then **Quit Claude** (or press Command+Q). Then open Claude Desktop again.

4. **Try it.** Ask Claude: "List my John Deere organizations."

---

## Step 2: Quick start on Windows

1. **Install `uv`.** Open PowerShell: click the Start button, type `PowerShell`, and choose **Windows PowerShell** from the results. PowerShell is a window where you type or paste commands. Paste the line below into it and press Enter. (To paste, press Ctrl+V or right-click inside the window.)

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   Close PowerShell and open it again so it knows about the new program. (If you prefer, `winget install --id=astral-sh.uv -e` does the same. `winget` is the tool Windows has for installing programs from the command line.)

2. **Run the setup.** Paste this into PowerShell and press Enter:

   ```powershell
   uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp setup
   ```

   `uvx` is the part of `uv` that downloads a tool and runs it. The first time, it can take a little while to download the tool. Then setup does these things, in this order:

   - It asks for your Application ID and then your Secret, the two codes from Step 1. Paste each one and press Enter. The Secret does not show on the screen as you paste it; that is normal. Setup saves both on your computer.
   - It opens your web browser so you can sign in to John Deere. The first time, John Deere also asks which of your organizations the application may see. Choose them and confirm.
   - It asks `Add the server to Claude Desktop? [Y/n]`. "The server" is this tool, the small program that Claude Desktop starts to talk to John Deere. In `[Y/n]` the capital letter is the default answer, so pressing Enter on its own means yes. Press Enter.
   - If Claude Code is installed on your computer, it also asks `Also add it to Claude Code? [Y/n]`. Press Enter for yes, or type `n` and press Enter if you do not use Claude Code.

   When PowerShell prints `Signed in. Quit and reopen Claude Desktop to finish.`, you are done with this step. Setup has written the entry that Claude Desktop needs, so there is no file for you to edit. If Claude Desktop already had other servers listed, they are kept, and setup saves a backup copy of the file first.

   If PowerShell prints only `Signed in.`, setup did not find Claude Desktop. Make sure Claude Desktop is installed, then follow [Manual configuration](#manual-configuration) near the end of this page, which takes the place of step 3. After that, continue with step 4.

3. **Quit and reopen Claude Desktop.** Closing the window is not enough, because the app can keep running in the background. Look for the Claude icon at the right end of the taskbar, near the clock. If you do not see it, click the small up arrow there to show hidden icons. Right-click the Claude icon and choose **Quit**. If there is no Claude icon, closing the window has already quit the app. Then open Claude Desktop again from the Start menu.

4. **Try it.** Ask Claude: "List my John Deere organizations."

---

## Step 2: Quick start on Linux

1. **Install `uv`.** Open a terminal (look for **Terminal** in your applications menu). A terminal is a window where you type or paste commands. Paste this line into it and press Enter:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Open a new terminal afterwards so it knows about the new program.

2. **Run the setup.** Paste this into the terminal and press Enter:

   ```bash
   uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp setup
   ```

   `uvx` is the part of `uv` that downloads a tool and runs it. The first time, it can take a little while to download the tool. Then setup does these things, in this order:

   - It asks for your Application ID and then your Secret, the two codes from Step 1. Paste each one and press Enter. The Secret does not show on the screen as you paste it; that is normal. Setup saves both on your computer.
   - It opens your web browser so you can sign in to John Deere. The first time, John Deere also asks which of your organizations the application may see. Choose them and confirm. If no browser opens, copy the link the terminal prints into one.
   - If Claude Code is installed on your computer, it asks `Also add it to Claude Code? [Y/n]`. In `[Y/n]` the capital letter is the default answer, so pressing Enter on its own means yes. Press Enter for yes, or type `n` and press Enter if you do not want it added.

   When the terminal prints `Signed in.`, you are done with this step.

3. **Connect your AI app.** Claude Desktop is not available on Linux. If you use Claude Code and answered yes in step 2, it is already connected. If you skipped it, use the command in the next section. For any other MCP-capable app, give it the command `uvx` with the arguments `--from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp`.

---

## Using it with Claude Code (any platform)

After Step 1 and the `setup` command for your platform, `setup` offers to add the server to Claude Code when it finds Claude Code on your computer. This is the `Also add it to Claude Code? [Y/n]` question. If you answered yes, there is nothing more to do here. If setup could not add it, it prints the command to run by hand.

If you skipped the question, or installed Claude Code later, run this once in a terminal:

```bash
claude mcp add --scope user deere -- uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp
```

Then start Claude Code and ask: "List my John Deere organizations."

---

## Everyday use

**Asking questions.** Just ask in plain words. The assistant picks the right tool. Field names, organization names and years all work.

**Signing in again.** Your sign-in normally renews itself. If the assistant reports that the session expired, run the setup command for your platform again, or just:

```bash
uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp login
```

**Signing out.** This removes the saved sign-in from your computer and tells John Deere to cancel it:

```bash
uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp logout
```

**Updating to the newest version.** Add `--refresh` once:

```bash
uvx --refresh --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp login
```

**If you move or reinstall things.** `setup` writes the full location (also called the path) of the `uvx` program into the Claude Desktop settings file. If you reinstall `uv` or move it, or you use a local copy of this project (for developers) and move its folder, that location goes out of date and Claude Desktop says the server could not start. Run this to write the entry again. It does not ask you to sign in again:

```bash
uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp connect
```

It asks the same yes or no questions as `setup`, then prints one line for each app saying what it did: created or updated the entry (with the name of the backup copy it saved first), found it already correct, or skipped the app because it was not found. If `setup` or `connect` ever prints a message ending in `Run "deere-mcp connect" to try again.`, your sign-in worked but the settings file was not changed. Fix what the message describes, then run the command above.

**Where your sign-in is kept.** In a folder only your user account can read:

| Platform | Folder |
|---|---|
| macOS | `~/Library/Application Support/deere-mcp` |
| Windows | `%LOCALAPPDATA%\deere-mcp` (usually `C:\Users\yourname\AppData\Local\deere-mcp`) |
| Linux | `~/.config/deere-mcp` |

It holds your Application ID, Secret and sign-in tokens. Do not copy it to other machines or share it. To see the exact folders on your computer, where your Claude Desktop settings file is and whether it has the `deere` entry, and whether Claude Code was found, run:

```bash
uvx --from https://github.com/vanantwerp/deere-ops-center-mcp/archive/refs/heads/main.zip deere-mcp config
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

**"redirect_uri mismatch" or "invalid redirect" during sign-in.** The Redirect URI in your John Deere application is not exactly `http://localhost:9090/callback`. Fix it there and run the sign-in command from Everyday use again.

**The organization list is empty, or you get "403 Forbidden".** The application has not been connected to your organization yet. Run the sign-in command from Everyday use again and complete the organization-selection page John Deere shows, or visit connections.deere.com and connect it there.

**Claude Desktop says the server could not start.** Run `connect` (the full command is under "If you move or reinstall things" in Everyday use), which writes the full path for you. Claude Desktop's own logs are under `~/Library/Logs/Claude` on macOS and `%APPDATA%\Claude\logs` on Windows.

**No browser opened during sign-in.** Copy the link printed in the terminal into any browser.

**"Session expired."** Run the sign-in command from Everyday use again.

**A question returned "list was cut short".** The result had more than the page cap allows. Narrow the question (a single season, a shorter date range) or raise `DEERE_PAGE_CAP`.

**Machine alerts or locations fail with a date error.** John Deere limits alerts to 7 days per request and location history to 1 month per request. Ask for a shorter window.

---

## Manual configuration

You only need this if `setup` could not find Claude Desktop, which you can tell because it never asked `Add the server to Claude Desktop? [Y/n]` and ended with just `Signed in.`

These steps do by hand what `setup` normally does for you: they tell Claude Desktop how to start this tool, by adding an entry to its settings file, `claude_desktop_config.json`. The file is written in a format called JSON, where every quote, comma and bracket matters, so copy the example exactly and change only the location you find in step 1.

### On macOS

1. **Find where `uvx` lives.** Claude Desktop does not look in the same places for programs that Terminal does, so it needs the full location. In Terminal:

   ```bash
   which uvx
   ```

   It prints something like `/Users/yourname/.local/bin/uvx`. Copy that.

2. **Edit the settings file.** Open Claude Desktop, go to **Settings → Developer → Edit Config**. The file `claude_desktop_config.json` opens. Make it look like this, using the location from the previous step:

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

   If the file already has an `mcpServers` section, add only the `"deere"` entry inside it, with a comma between entries, and keep what is already there. Save the file.

3. **Quit and reopen Claude Desktop.** Follow step 3 of the macOS quick start. Then continue with step 4 there.

### On Windows

1. **Find where `uvx` lives.** Claude Desktop does not look in the same places for programs that PowerShell does, so it needs the full location. In PowerShell:

   ```powershell
   where.exe uvx
   ```

   It prints something like `C:\Users\yourname\.local\bin\uvx.exe`. Copy that.

2. **Edit the settings file.** Open Claude Desktop, go to **Settings → Developer → Edit Config**. The file `claude_desktop_config.json` opens. Make it look like this, using the location from the previous step. In this file every backslash in a path is written twice:

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

   Leave the `"env"` line in. It makes the tool use UTF-8, the standard way of storing text, including accented characters, for what it sends back to Claude Desktop. If the file already has an `mcpServers` section, add only the `"deere"` entry inside it, with a comma between entries, and keep what is already there. Save the file.

3. **Quit and reopen Claude Desktop.** Follow step 3 of the Windows quick start. Then continue with step 4 there.

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
