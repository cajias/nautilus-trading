# VHS tape for nautilus-trading — renders assets/demo.gif
# Run:  vhs assets/demo.tape   (from repo root, after `make install`)

Output assets/demo.gif

Set Shell "bash"
Set Width 1200
Set Height 700
Set FontSize 18
Set Theme "Dracula"
Set Padding 20

# Enter the uv-managed package so the `nt` CLI resolves.
Type "cd nautilus" Enter
Sleep 500ms

# 1) The CLI surface: backtest, paper-trade, live (scaffold), strategies.
Type "uv run --no-sync nt --help" Enter
Sleep 4s

# 2) Discover every strategy spec + its source distribution.
Type "uv run --no-sync nt strategies" Enter
Sleep 4s

# 3) Inspect the config-driven backtest command.
Type "uv run --no-sync nt backtest --help" Enter
Sleep 4s
