# Codex Smart Paste

`Cmd+V` in the VS Code integrated terminal behaves like this:

- text clipboard: use the normal terminal paste action
- image clipboard on macOS: save the image to `/tmp/codex-paste-images/<timestamp>.png` and paste the absolute path

## Why this exists

The current terminal workflow cannot rely on inline image attachments everywhere. Pasting an absolute image path is the most reliable one-keystroke fallback for Codex-style terminal sessions.

This extension uses the macOS system `swift` runtime plus `AppKit`, so it does not need `pngpaste` or any npm dependencies.

## Install locally

This extension is plain JavaScript and does not need a build step.

1. Symlink this folder into `~/.vscode/extensions/local.codex-smart-paste-0.0.1`
2. Reload VS Code
3. Focus the terminal and press `Cmd+V`

Example symlink command:

```bash
ln -s "/Users/yunjitaegi/HQ/workspace/Projects/youtube/yt-youtube-production/tools/codex-smart-paste" \
  "$HOME/.vscode/extensions/local.codex-smart-paste-0.0.1"
```

## Notes

- The keybinding is contributed by the extension, so no manual `keybindings.json` edit is required.
- The clipboard image capture path can be changed with `codexSmartPaste.tempDir`.
