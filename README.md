# ZeroScript - Free AI Agent for Roblox Studio

![GitHub stars](https://img.shields.io/github/stars/sebattfg/ZeroScript-Free?style=social)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey)
![License](https://img.shields.io/badge/license-GPL--3.0-blue)

**ZeroScript** is a free browser extension that turns ChatGPT, DeepSeek, Gemini, Kimi, GLM, Qwen, Arena or Meta AI into a Roblox Studio AI agent.
Control Roblox Studio with AI directly from your browser - read/edit scripts, run Luau, generate assets, all from a normal AI chat. No API key, no terminal, no coding needed.

> 🌐 **Website: [zerodev.tools/zeroscript](https://zerodev.tools/zeroscript)** the free Lemonade.gg / Luamotion alternative for building Roblox games with AI.

Eight AI providers are supported: **DeepSeek** (chat.deepseek.com, recommended), **ChatGPT** (chatgpt.com), **Google Gemini** (gemini.google.com), **Kimi** (kimi.ai, Moonshot AI), **GLM** (chat.z.ai, Z.ai), **Qwen** (chat.qwen.ai), **Arena** (arena.ai, a multi-model playground) and **Meta AI** (meta.ai). On ChatGPT, screenshots and image input are turned off on purpose: the free tier limits files and images on a separate quota from messages, so vision would only work part of the day. Gemini and Kimi can be unstable: Gemini tends to stop using the Roblox tools in long sessions, and Kimi sometimes uses its own native tools instead of the Roblox commands. On Arena, use **Direct** mode (ZeroScript only supports Direct; it blocks Start in Battle / Side-by-Side / Agent modes). DeepSeek is the recommended provider.

> 💬 **Stuck? Join the [Discord community](https://discord.gg/9aNyZsMWcb)** get help, share feedback, and follow updates.

> *Also known as: ZeroScript Roblox, ZeroScript free download, Roblox ChatGPT agent, Roblox DeepSeek agent, Roblox Gemini agent, Roblox Kimi agent, Roblox GLM agent, Roblox Qwen agent, Roblox Arena agent, Roblox Meta AI agent, Roblox Studio AI automation, Luau AI, MCP Roblox, lemonade alternative free, lemonade.gg alternative, free Roblox AI agent, free lemonade roblox alternative*

## ⚠️ ZeroScript is Free Beware of Paid Copycats

ZeroScript is 100% free and open-source. It always has been, and it always will be. There is no official paid version, no subscription, and no sign-in required to use the extension.

If you come across a site or extension using the ZeroScript name that asks for payment or account creation, it is **not** this project. The only official links are the ones listed at the top of this README.

## How it works

```
AI chat (ChatGPT / DeepSeek / Gemini / Kimi / GLM / Qwen / Arena / Meta AI, in your browser) -> ZeroScript Extension -> Bridge (your PC) -> Roblox Studio
```

The extension runs inside the chat page (ChatGPT, DeepSeek, Gemini, Kimi, GLM, Qwen, Arena or Meta AI). When you type a request, it sends commands to the Bridge running on your PC, which drives Roblox Studio through the built-in MCP server.

## Setup

> 📺 **Lost? Watch the [setup tutorial on YouTube](https://youtu.be/kPKiZLZ9_Ps) it covers every step below.**

### 1. Download the zip and install the extension

Download the latest zip from the **Releases** page and extract it. The zip contains both the **Bridge** and the **extension folder**.

To load the extension:

- Go to `edge://extensions` (Edge) or `chrome://extensions` (Chrome)
- Enable **Developer mode** (top right toggle)
- Click **Load unpacked**
- Select the `zeroscript-extension` folder from the extracted zip

### 2. Start Roblox Studio and enable MCP

Open Studio and load a Place, then enable MCP (first time only):

- Click **Assistant AI** in the top bar
- Click **...** (top right of the Assistant panel)
- Click **Manage MCP Servers**
- Click **Enable Studio as MCP Server**

> Not sure where to find these options? The [video tutorial](https://youtu.be/kPKiZLZ9_Ps) shows exactly where to click.

### 3. Run the Bridge

- **Windows:** double-click `start.bat` inside the extracted folder.
- **macOS:** double-click `MacOS_Start.command` inside the extracted folder. The first time, macOS will show a security warning ("could not verify... free of malware") - this is normal for any script downloaded outside the App Store, click **Done**, then go to **System Settings > Privacy & Security**, scroll to the bottom, and click **Open Anyway**. You only need to do this once.

A small window opens, that means the Bridge is running.

### 4. Start a session

Go to https://chat.deepseek.com (recommended), https://chatgpt.com, https://gemini.google.com, https://www.kimi.ai, https://chat.z.ai, https://chat.qwen.ai, https://arena.ai or https://www.meta.ai and open a new chat. The ZeroScript bar appears above the input box. Click **Start session**. Type what you want to build.

> Only works on chat.deepseek.com, chatgpt.com, gemini.google.com, kimi.ai, chat.z.ai, chat.qwen.ai, arena.ai and meta.ai - it will not work on any other site.
> On Arena, keep the mode dropdown on **Direct** - ZeroScript blocks Start in Battle / Side-by-Side / Agent modes (it only drives a single Direct reply).
> Gemini and Kimi can be unstable (model behavior, not the extension): Gemini may stop using the Roblox tools after a while, and Kimi may use its own native tools instead. If the AI starts answering in plain text instead of acting, remind it to use the commands or start a new session.
### 5. Watch the setup tutorial

[Watch the setup tutorial on YouTube](https://youtu.be/kPKiZLZ9_Ps)

## What the AI can do

- Read and edit scripts
- Run Luau code directly in Studio
- Inspect the game tree and instances
- Generate meshes, materials, and models
- Browse and insert from the Creator Store
- Control play-testing
- **Remember your project across sessions** persistent project memory saved inside your place

## Using ZeroScript with Blender (experimental)

ZeroScript is built for Roblox Studio, but it can also drive **Blender** through an addon MCP server alongside Roblox Studio. It works the same way: the AI in your chat writes commands, ZeroScript runs them on your PC through the bridge.

1. **Install the uv package manager** (the Blender server is launched with `uvx`): follow the instructions at [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) for your OS.
2. **Open the ZeroScript menu** in any supported AI chat (Start a session first, then click the **⋯** button), scroll to **MCP servers**, click the **Blender** preset, then press **Add server** twice (the second click confirms the exact command - it will run on your PC). The bridge connects to it live - no restart. The first connect can take about a minute while uv downloads the pinned server package (`mcp-for-blender@2.3.0`). To update the pin later, change the `@version` in the preset's command after checking the release notes at github.com/ahujasid/mcp-for-blender.
3. **Enable the MCP for Blender add-on inside Blender:** run `uvx mcp-for-blender install-addon` in a terminal, then in Blender go to **Edit > Preferences > Add-ons** and enable **Interface: MCP for Blender**. Start the add-on's connection from its sidebar panel and keep Blender open while you use it.
4. Ask for what you want built. The AI discovers Blender's commands on its own through `list_mcp_servers` and `list_commands`.

Notes:

- Both tools stay connected at once: Roblox Studio is the primary server, Blender is an addon - asking about Blender automatically routes to it.
- **Safe mode is on by default** (the preset sets `BLENDER_MCP_SAFE_MODE=1`): the Blender MCP server itself rejects hostile Python (eval/exec/os/subprocess/network/persistence) before anything reaches Blender, verified in the package's source (v2.3.0). ZeroScript additionally shows a **consent dialog** the first time the AI wants to run code through Blender (and once per session at most after "Allow all session") - the prompt is rendered by ZeroScript, never asked through the AI site. Telemetry collection in the package is kept off via `DISABLE_TELEMETRY`.
- The panel shows Blender's health next to Roblox Studio's. A red dot with a reason (uv missing, Blender closed, add-on not started) names the exact fix.

### Known limitations of the Blender integration (documented honestly)

- The MCP for Blender add-on does **not** wrap each AI action in an undo step (verified in its source, v2.3.0): to revert AI changes, use Blender's undo (Ctrl+Z repeatedly) or reopen without saving.
- The add-on's own local socket (port 9876) accepts commands from any local process regardless of safe mode - that is the package's design, outside ZeroScript's control.
- A long render/bake blocks its tool call until done (the package has no progress events) - ask for small renders or poll job tools where provided.

## Bridge security

The bridge listens on `ws://127.0.0.1:17613` on YOUR machine only. It authenticates every connection: WebSocket clients must present a per-install token (stored in `bridge_auth.json` next to the bridge, created automatically) as the first message, and the handshake additionally rejects any web page's origin (a page in your browser cannot connect or read the token) plus any non-loopback Host header. Rotation: delete `bridge_auth.json` and restart the bridge (the extension re-pairs automatically), or send `GET http://127.0.0.1:17613/zs-auth/rotate` from an extension context. A process running as your own OS user is inside this trust boundary and can read the token file - the boundary is your user account, exactly like the rest of the bridge.

## New in 1.5.5

- **DeepSeek: the agent starts again on the new unified model.** DeepSeek merged Instant, Expert and Vision into one model and removed the model picker, which left "Start Roblox agent" stuck on "DeepSeek mode not ready". ZeroScript now recognises the new chat box, switches Search off and starts, with DeepThink left on.
- **DeepSeek: screenshots work on every chat.** Images no longer need the Vision tab (it is gone) - the unified model sees your Studio captures, one at a time or several in a row.

## New in 1.5.4

- **ChatGPT: the ZeroScript bar is back above the composer.** ChatGPT redesigned its input box and renamed the layout slot the bar sits in. ZeroScript kept asking for the old name, so the browser dropped the bar into a stray strip at the bottom right of the composer and squeezed the text field to nothing. The bar now takes the right row again, and it reads the layout live instead of trusting a fixed name, so the next redesign should not knock it out.
- **ChatGPT: long commands read cleanly on the new interface.** The same redesign replaced the code-block editor that used to hide line breaks and cut long lines off - the cause of the truncated commands fixed in 1.5.1. A 400-line block now reads back whole. If you are still on the old interface, the previous workaround is untouched.

## New in 1.5.3

- **Kimi moved to kimi.ai.** The old address, kimi.com, now asks for a Chinese phone number to sign in, which locked most people out. Open https://www.kimi.ai instead - the page is unchanged, the bar appears above the input box exactly as before. Reopen any Kimi tab you had on the old address.
- **DeepSeek: the Instant model can now run the agent.** Picking Instant used to leave "Start Roblox agent" spinning forever with no explanation, because only Expert and Vision were accepted. Choose Instant before starting and the session runs on it - much faster than Expert, without the reasoning pass. Images stay off on Instant just like on Expert; the Vision tab remains the only one that can see screenshots.
- **DeepSeek: a reply written in DeepSeek's own tool-call format no longer kills the turn.** DeepSeek occasionally answers with its internal markup instead of a ZeroScript command. Nothing recognised it, so the tool never ran, the raw tags stayed on screen and the agent stopped dead with you waiting. It is now caught, hidden behind a tool chip like any other command, and DeepSeek is told to rewrite the call properly.
- **ChatGPT: the bar no longer clips into the composer's rounded corners.**

See [CHANGELOG.md](CHANGELOG.md) for older releases.

## Panel status

| Dot | Meaning |
|-----|---------|
| Green | Bridge + Studio ready (a place is open) |
| Yellow | Bridge OK, but Studio isn't usable yet - open Roblox Studio, load a place, or enable its MCP server (hover the dot for the exact reason) |
| Grey | Bridge offline - run start.bat (Windows) or MacOS_Start.command (macOS) |

## Requirements

- Windows or macOS
- Roblox Studio (MCP support built-in)
- Microsoft Edge or Chrome
- Python 3.9+ (installed automatically on Windows, or install it yourself on macOS - see [python.org/downloads](https://www.python.org/downloads/))

## Support

ZeroScript is free. If it saves you time: [Ko-fi](https://ko-fi.com/sebattfg) - Robux tip passes available in the extension panel

---

Credit: the idea for connecting other MCP servers (Blender, Sketchfab, etc.) alongside Roblox Studio came from [javnpa](https://github.com/javnpa).

Credit: macOS/Linux support contributed by [archivealf](https://github.com/archivealf).
