# activitysim

A small program for macOS and Windows that looks like someone is using the
machine. It checks out a public GitHub repository and opens that folder in
its **own** Visual Studio Code window, then uses a separate signed-out Google
Chrome for GitHub and, less often, public benchmark sites. The clicks and
keystrokes are real input events. An idle check that only watches the pointer
and keyboard will see them. Changing a tab with AppleScript alone will not.

A normal run prints `launching dev browser` and then stays quiet. Errors are
printed on their own.

That Chrome is a separate process with an empty profile. It does not see the
cookies, passwords, or keychain of the Chrome you are signed into. X and
Twitter do not resolve in it.

## macOS

It is meant to run on your own Mac while sleep is disabled. It does not change
power settings. Pair it with the `pmset` command below.

```bash
sudo pmset -a disablesleep 1
python3 -m activitysim
```

Stop it with Ctrl+C. The signed-out Chrome window stays open. Restore sleep later:

```bash
sudo pmset -a disablesleep 0
```

Run `activitysim` as your user. Do not put `sudo` in front of it. `sudo` is
only for `pmset`.

## Windows

Install Python 3.9 or newer and Google Chrome, then:

```bash
python -m activitysim
```

Stop it with Ctrl+C. The signed-out Chrome window stays open.

`--caffeinate` calls `SetThreadExecutionState` and keeps this process awake
until it exits. It does not change the power plan.

Typed characters assume a US keyboard. Run as the normal user, not as
Administrator. Back in the browser is Alt+Left. The address bar is Ctrl+L.

## What a session does

- Asks for Accessibility if the pointer cannot move yet, opens that Settings
  pane, and waits until the switch is on.
- Checks out one modest public repository with a shallow `git clone` into
  its own folder, then opens that folder in a separate Visual Studio Code
  window. That window uses activitysim's profile, not the Code you are
  signed into. It opens source files with Quick Open, scrolls, moves through
  the text, and sometimes searches in the file. It does not save. If that
  window opens, the program prints `launching dev browser` and then stays
  quiet. Your Chrome and your own Code stay as they were.
- Later chapters keep that mix: about six in ten are the editor, about three
  in ten are GitHub, and about one in ten is a benchmark site. GitHub is a
  repository it has not already opened, or an advanced search. A repository
  visit opens the project, a source file when the file tree is on screen,
  then issues, pull requests, or commits.
- A benchmark visit is short. The site is one of DeepSWE, ProgramBench,
  SWE-bench, Terminal-Bench, LiveCodeBench, SWE-bench Live, or SWE-smith.
  If Visual Studio Code or git is missing, the editor chapters stay on GitHub
  instead of moving onto those sites.
- On each page, scrolls down and pages through the text, then clicks in the
  page body. Reading the open page waits less than changing pages. The
  default quiet stretch between page changes is 0.45s to 1.6s.
- Presses Command+Left to go back in Chrome, then continues. In the editor,
  Command+P opens a file and Command+F searches. Neither chord is sent to Chrome.

Your signed-in Chrome is not controlled. If that Chrome is the one in front,
the simulator backs off instead of clicking it.

## What it will not do

- It will not open a window in the Chrome that is signed into X or GitHub.
- It will not open your signed-in Visual Studio Code profile, and it will not
  open a folder outside its own checkout.
- It will not read that Chrome's cookies, passwords, or the login keychain.
- It will not save files in the editor, and it will not type there unless
  that window is in front.
- It will not open X or Twitter. Those hosts do not resolve in the signed-out Chrome.
- It will not click the right side of a result row, where Star, Fork, and Watch sit.
- In Chrome it types only into the GitHub search box or the address bar, and only while its own Chrome is in front. In the editor it types a source path into Quick Open, or a short find token, and only while that window is in front.
- It will not send Cmd+Q, Cmd+W, or Space. Space would activate a focused button. Cmd+W would close a tab.
- It will not open `javascript:`, `file:`, or URLs that contain a username and password.
- It backs off for 30 seconds if you move the mouse or switch away from the app it is driving. `--pause 0` turns that off.
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
python3 -m activitysim run --close-on-exit
python3 -m activitysim run --urls urls.example.txt
python3 -m activitysim run --dry-run --for 1m --seed 7
python3 -m activitysim run --caffeinate
```

`--min-gap 4 --max-gap 15` slows the session down. The defaults stay short so
the pointer keeps moving. Reading the page that is already open waits less
than that range.

`--for` accepts `30s`, `45m`, `2h`, or `forever` (the default). `--caffeinate`
asks macOS to ignore idle sleep until the program exits, without the persistent
`pmset` change. On Windows, `--caffeinate` calls `SetThreadExecutionState`
and does not change the power plan. `--dry-run` prints a session and touches nothing.

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

Use whatever repository name you want. On macOS the program depends on macOS,
Python, and Google Chrome. On Windows it depends on Windows, Python, and
Google Chrome.
