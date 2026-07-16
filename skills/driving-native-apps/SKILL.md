---
name: driving-native-apps
description: Drive a native GUI app on this machine — snapshot a window's accessibility tree, then click/type/scroll by element, without stealing the user's focus. Use when asked to operate, drive, automate, or do something in a real desktop application (or a web app that only exists in a real browser window). Requires the cua plugin's cua-driver__* tools.
---

# Driving native apps

You can operate real applications on this machine through the `cua-driver__*`
tools. This is not a browser automation library and not a screenshot loop — it
reads the app's **accessibility tree** and acts on **elements**, which is why it
works on apps that have no API and why it doesn't need to bring anything to the
foreground.

Read this before your first tool call. The core invariant below is not a style
preference; skipping it fails **silently**, and the failure looks like the tools
being broken rather than like a protocol mistake.

## The core invariant — snapshot before AND after every action

**Every action must be bracketed by `get_window_state(pid, window_id)`.**

**Before**, because that snapshot is what mints the `element_index` values you're
about to use. The index map is rebuilt on every snapshot and keyed on
`(pid, window_id)`. So:

- an index from a previous turn does not resolve in this one;
- an index from window A does not resolve against window B of the same app;
- acting on a stale index fails with `No cached AX state`.

**After**, because it's your only evidence the action actually landed. A click on
the wrong element, a disabled button, a dialog that ate the keystroke — none of
these raise. The accessibility tree changing (a new value, a new window, a menu
that closed, a button that greyed out) is the proof. **If nothing changed, the
action probably didn't fire. Say so — don't report success you didn't observe.**

This applies to pixel clicks too.

## Never steal the user's focus

Someone is using this machine right now. The driver exists specifically to act on
background windows, and the entire value of it evaporates if you yank the
frontmost app out from under them.

- Do **not** shell out to `open`, `osascript` that mutates GUI state, `cliclick`,
  or any AppleScript `activate`.
- Do **not** reach for `run_command` to do something a `cua-driver__*` tool does.
  If you're about to, you're about to steal focus.
- If you find yourself wanting "activate", "foreground", "raise", or "make key",
  stop and find the tool that expresses the same intent without focus.

`launch_app` starts an app the right way. Prefer it over any shell invocation.

## The loop

```
list_apps()                       → find the app, get its pid
  (or launch_app({bundle_id}))    → starts it, returns {pid, windows:[...]}
list_windows({pid})               → pick the window_id YOURSELF (see below)
get_window_state({pid, window_id})   ← snapshot: read the tree, find your element
click({pid, window_id, element_index})
get_window_state({pid, window_id})   ← snapshot: confirm it landed
```

**Window selection is your job.** There is no "just pick the main one" — an app's
biggest window is routinely an off-screen utility panel, and a heuristic that
guesses lands your clicks in an invisible surface while reporting success. Call
`list_windows`, choose deliberately, and carry that `window_id` through the whole
sequence.

## Acting

Prefer elements over pixels. `element_index` from a fresh snapshot is stable,
self-describing, and survives the window moving. Pixel coordinates are a fallback
for canvas-like surfaces the tree doesn't describe.

- Pointer: `click`, `double_click`, `right_click`, `drag`, `scroll`
- Text: `type_text` types into the focused element; `set_value` sets a field's
  value directly — usually better than typing when the element accepts it.
- Keys: `press_key` for one key, `hotkey` for a chord.
- `zoom` crops and magnifies a window region when you need to *see* something the
  tree doesn't spell out.

Capture note: there is **no `screenshot` tool** — it was removed upstream. To see
a window, use `get_window_state` with `capture_mode: "vision"`. If you read cua
documentation elsewhere that tells you to call `screenshot`, it's out of date.

## When it goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `No cached AX state` | index is stale, or from another window | re-snapshot the exact `(pid, window_id)`, use fresh indices |
| Action returns fine, nothing changed | silent no-op — wrong element, or it's disabled | re-snapshot, compare, report the no-op honestly |
| Clicks land in the wrong place | you're driving an off-screen window | `list_windows`, pick the visible one |
| Tools missing entirely | driver not installed / not permitted | check Settings ▸ Computer use ▸ Test connection |
| Element you can see isn't in the tree | sparse tree (Electron, Tauri, canvas) | `zoom` to look, then pixel-click; re-snapshot after |

## What you're actually touching

This drives the user's **real** desktop — their real files, their real logged-in
sessions, their real accounts. protoAgent's filesystem fence and network egress
allowlist do not apply to anything you do here; they're checks inside
protoAgent's own tools, and a mouse click never reaches them.

So: stay inside the task you were asked to do. Don't wander into apps you weren't
sent to, don't act on anything that looks like credentials, payment, or
destructive confirmation without checking in first, and if a window turns out to
hold something sensitive you didn't expect, stop and say so rather than reading
on. When an action is irreversible — sending, posting, deleting, paying — confirm
before you fire it, not after.
