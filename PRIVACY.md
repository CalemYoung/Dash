# Privacy

Dash has no telemetry, no analytics and no account. It does not send your
commands, your searches or your usage anywhere.

## Network requests

Dash connects to the internet only for these things:

| What | Where it connects | How to turn it off |
| --- | --- | --- |
| Update check, once each time Dash starts and when you choose "Check for Updates..." | `api.github.com` | Settings: "Check updates at startup" |
| Downloading an update, only after you choose "Install and Restart" | `github.com` and GitHub's download servers | Do not choose it |
| Website icons for website commands | The website itself (`/favicon.ico`), Google's favicon service (`www.google.com/s2/favicons`) and DuckDuckGo's (`icons.duckduckgo.com`). Each is sent the website's host name. | Settings: "Download website icons" |
| Checking that a website address works, while you type one into the command editor | The website you typed | Do not use a website command |
| Web search, only when you choose the "search the web" result | The search engine you picked in Settings, opened in your browser | Only when you choose that result |

Opening a website command opens it in your browser; Dash itself does not
fetch the page.

## Data on your computer

Everything Dash keeps stays on your computer, in `%APPDATA%\Dash`:

- `config\settings.toml` and `config\commands.toml`: your settings and
  commands.
- `config\run_counts.json`: usage counts (how often and when you last opened
  each command), used for "most used first" sorting. Clear them under
  Settings.
- `assets\icons`: icons you chose and cached website icons.
- `logs\dash.log`: a log of what Dash did and any errors, kept to a few
  small files. It can contain the names and locations of your commands.
  Nothing sends it anywhere; attach it to a bug report only if you want to.

Installers downloaded by the updater are kept in `%TEMP%\Dash\updates`.

When you uninstall Dash, you are asked whether to delete `%APPDATA%\Dash`
as well. Silent uninstalls (such as `winget uninstall`) keep it.
