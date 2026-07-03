# iCloud Sync

A Linux desktop control panel for keeping **multiple iCloud Drive folders** in
sync via [rclone](https://rclone.org/) — with background syncing that keeps
running after you close the window.

![screenshots coming soon](docs/screenshot-placeholder.png)

## How it works

The app is two programs sharing one config:

- **`icloud-sync`** — a [NiceGUI](https://nicegui.io/) desktop app: add/edit
  sync folders, run Pull / Push / Bisync with live progress bars and logs,
  manage schedules, and reconnect when Apple invalidates your session.
- **`icloud-sync-runner <folder-id>`** — a headless runner that does the
  actual syncing. systemd user timers invoke it in the background; the GUI
  spawns the same runner for manual syncs. Each run appends to a per-folder
  log and state file under `~/.local/state/icloud-sync/`, which is how the
  GUI shows identical live progress for manual *and* scheduled runs.

Because syncs never live inside the GUI process, closing the window never
interrupts a transfer.

## Prerequisites

- Linux with systemd (user session) — tested on Ubuntu
- [rclone](https://rclone.org/install/) ≥ 1.68 with a **working iCloud Drive
  remote** — set one up first with `rclone config` (see the
  [iCloud Drive backend docs](https://rclone.org/iclouddrive/)) and verify it
  with `rclone lsd your-remote-name:`
- [uv](https://docs.astral.sh/uv/)

## Install & run

```bash
git clone https://github.com/d3vmeh/icloud_sync_app
cd icloud_sync_app
uv sync
uv run icloud-sync            # opens the control panel
```

For a native desktop window (instead of a browser tab), install the optional
webview extra — it needs Qt libraries on your system:

```bash
uv sync --extra native
```

Without it (or with `--browser`), the panel opens in your default browser.

### Background syncing

Each folder card has two schedule toggles, both implemented as systemd user
units generated and managed by the app (no hand-editing):

- **Sync on startup** — enables `icloud-sync@<folder-id>.service` on login.
- **Sync every N minutes** — creates and enables `icloud-sync@<folder-id>.timer`.

To keep timers running while you're logged out, click **Enable lingering**
when the app suggests it (this runs `loginctl enable-linger`).

You can also invoke the runner directly, which is exactly what systemd does:

```bash
uv run icloud-sync-runner <folder-id> [--action pull|push|bisync] [--dry-run]
```

Overlapping runs are safe: the runner takes a per-folder lock and exits early
if a sync for that folder is already in progress.

## The 2FA / trust_token caveat

Apple invalidates the iCloud `trust_token` roughly **once a month**. When that
happens, background syncs start failing silently — there is no way around
this, it's how Apple's authentication works. This app handles it by:

- detecting authentication failures in the runner and flagging the folder,
- showing a red **Reconnect required** banner the next time you open the
  panel, and
- providing an in-app reconnect dialog that runs
  `rclone config reconnect your-remote:` and prompts you for the 6-digit
  code sent to your Apple device.

## Where your data lives

The app never reads or stores your Apple ID, password, or tokens — those stay
in rclone's own config (`~/.config/rclone/rclone.conf`), and the app refers to
the remote **by name only**.

| What | Where |
|---|---|
| Folder list / settings | `~/.config/icloud-sync/config.json` |
| Per-folder logs | `~/.local/state/icloud-sync/<folder-id>.log` |
| Per-folder state | `~/.local/state/icloud-sync/<folder-id>.json` |
| Generated systemd units | `~/.config/systemd/user/icloud-sync@*` |

A [`config.example.json`](config.example.json) shows the config shape with
placeholder values.

### Folder layout

rclone syncs a directory's *contents*, so pulling `Documents/MyFolder` into
`~/sync` would normally scatter its files directly into `~/sync`. Each folder
has a **“Keep the remote folder name locally”** toggle (on by default for new
folders) that recreates the remote folder itself — files land in
`~/sync/MyFolder` instead. The dialog shows the effective target path as you
type.

### Launch from the dock

```bash
uv run icloud-sync --install-desktop
```

installs an app icon and launcher entry (`~/.local/share/applications/`), so
iCloud Sync shows up in your app grid — from there, right-click it and
choose **Pin to Dash / Add to Favorites** to keep it in the dock.
`--uninstall-desktop` removes it again.

### System tray

The app adds a tray icon with quick **Pull** / **Push** actions per folder,
plus show/hide window and quit. Quick actions run through the same background
runner, so their progress appears in the panel like any other sync. On GNOME
you may need the
[AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/)
for the icon to be visible; the app works fine without a tray.

### Excluding files

Each folder takes a list of exclude patterns
([rclone filter syntax](https://rclone.org/filtering/)) — useful for keeping
things like `node_modules/**` or `.git/**` out of your syncs. Set them in the
folder's edit dialog, one per line. Changing the patterns on a bisync pair
automatically triggers `--resync` on its next run, since rclone would
otherwise misread newly-excluded files as deletions.

### Bisync safety

Two-way sync uses `rclone bisync` with `--conflict-resolve newer`; the first
run of a pair automatically uses `--resync`. Enabling **Bisync safety
markers** on a folder adds `--check-access` with `RCLONE_TEST` marker files
on both sides, so a network blip can't be misread as "everything was deleted".

## Development

```bash
uv sync
uv run pytest                       # unit tests
uv run pre-commit install          # gitleaks secret scan + hygiene hooks
uv run icloud-sync --browser       # run the panel in a browser
```

Contributions welcome — please keep the repo free of personal data (remote
names, paths, tokens) and run `uv run pre-commit run --all-files` before
submitting a PR.

## License

[MIT](LICENSE)
