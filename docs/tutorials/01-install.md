# 1. Install and connect your assistant

You need a Mac with macOS 14 or later, and Codex (the ChatGPT app) or Claude Desktop signed in
on your own plan. Vademecum uses that assistant as its model. It never asks for an API key.

## Install

```sh
git clone https://github.com/nickbrazeau/Vademecum.git
cd Vademecum
./scripts/install.sh
```

The installer says what it does as it goes:

1. Finds Python 3.12 or newer. If there is none, it offers to install it with Homebrew.
2. Installs the Vademecum server and its MCP server into a virtualenv inside the checkout.
3. Builds the web app if Node is installed. Otherwise it uses the build in the checkout.
4. Asks where your **source folder** should be and lays it out. This is where your material goes.
5. Registers Vademecum with Codex and with Claude Desktop, whichever you have.
6. Offers to start Vademecum at login, so the web app is always ready.

## Open the web app

Go to <http://127.0.0.1:8765> in Safari. To keep it one click away, choose **File → Add to Dock**.

## Choose the Mac's model connection

Open **Settings → Model and allowance**. It shows which assistant does the model work on the Mac
(Codex or Claude), whether it is signed in, and how much of your plan's allowance is left.

You can also set it from the terminal:

```sh
./scripts/mcp.sh setup login --model codex    # or --model claude
```

## In the assistant

Restart the ChatGPT app or Claude Desktop, then ask:

> Show me my Vademecum cover sheet.

The assistant then has Vademecum's tools: Today, the tutors, flags, the map and builds.

Where your data lives:

- **Your material**: the source folder you chose.
- **Your records**: `~/Library/Application Support/Vademecum`. Export and backup are at the foot
  of **Sources**.

Never put patient identifiers in the folder or in the chat.

Next: [Add your first sources](02-first-sources.md).
