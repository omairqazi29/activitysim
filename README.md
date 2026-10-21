# activitysim

A small macOS program that looks like someone is using the machine. It opens a
**new** Google Chrome window, runs a GitHub advanced search, and clicks through
the results. The clicks and keystrokes are real input events. An idle check
that only watches the pointer and keyboard will see them. Changing a tab with
AppleScript alone will not.

It is meant to run on your own Mac while sleep is disabled. It does not change
power settings. Pair it with the `pmset` command below.

```bash
sudo pmset -a disablesleep 1
python3 -m activitysim
```

Stop it with Ctrl+C. The Chrome window stays open. Restore sleep later:

```bash
sudo pmset -a disablesleep 0
```

Run `activitysim` as your user. Do not put `sudo` in front of it. `sudo` is
only for `pmset`.

## What a session does

- Asks for Accessibility if the pointer cannot move yet, opens that Settings
  pane, and waits until the switch is on.
- Opens one new Chrome window on GitHub advanced search.
- Clicks the search box, types a query such as `language:python stars:>1000`,
  and presses Return. Typed characters assume a US keyboard.
- Clicks repository rows in the results, a few seconds apart.
- On each result, presses arrow keys, scrolls, and clicks in the page body.
- Presses Command+Left to go back, then clicks the next result.
- Starts another advanced search after a handful of results.

Your other Chrome windows are not controlled.

## What it will not do

- It will not click the right side of a result row, where Star, Fork, and Watch sit.
- It types only into the GitHub search box or the address bar, and only while Chrome is in front.
- It will not send Cmd+Q, Cmd+W, or Space. Space would activate a focused button. Cmd+W would close a tab.
- It will not open `javascript:`, `file:`, or URLs that contain a username and password.
- It backs off for 3 minutes if you move the mouse or switch away from Chrome after it had Chrome in front. `--pause 0` turns that off.
- It will not run as root.

## Permissions

The first run asks you to let the terminal control Google Chrome. Allow it.

Pointer and keyboard events need Accessibility. `activitysim` asks macOS itself
and opens System Settings if the pointer does not move. Turn on the app that
launched it (Terminal, iTerm, or Cursor). The program waits and then continues.
If the terminal is already enabled and the pointer still does not move, also
enable the Python binary printed in the log.

Check it:

```bash
python3 -m activitysim doctor
```

`doctor` shows the permission dialog, nudges the pointer by 2 pixels, and puts
it back. Without Accessibility the program stops. It will not pretend that
opening tabs is activity.

## Other ways to run it

```bash
python3 -m activitysim run --for 2h
python3 -m activitysim run --min-gap 4 --max-gap 15 --max-tabs 4
python3 -m activitysim run --mouse-only
python3 -m activitysim run --no-keyboard
python3 -m activitysim run --incognito
python3 -m activitysim run --close-on-exit
python3 -m activitysim run --urls urls.example.txt
python3 -m activitysim run --dry-run --for 1m --seed 7
python3 -m activitysim run --caffeinate
```

`--for` accepts `30s`, `45m`, `2h`, or `forever` (the default). `--caffeinate`
asks macOS to ignore idle sleep until the program exits, without the persistent
`pmset` change. `--dry-run` prints a session and touches nothing.

There are no third-party packages. Python 3.9 or newer is enough. Google Chrome
has to be installed for browsing. `--mouse-only` does not need Chrome.

```bash
python3 -m unittest discover -s tests
```

## Share it

The license is MIT. From this directory:

```bash
git init
git add .
git commit -m "Add activitysim"
gh repo create activitysim --public --source=. --push
```

Use whatever repository name you want. The program only depends on macOS,
Python, and Google Chrome.
