# Tests

The original kit was exercised with scripted mock-agent rooms (agents
that misbehave on purpose: bad formats, invented watcher phrases,
copied delivery directions) and with live rooms run by a small model.
Those live runs produced transcripts, scratch files, records and
audio containing the original production's real show content, so the
run outputs are NOT included in this public copy, and no standalone
test harness shipped with the internal kit to include.

What you can run without any API key:

```
python moe.py doctor --docs templates   # environment + governing-doc check
python -m compileall moe.py moekit      # the code parses
```

To smoke-test a room end to end, copy `examples/one-on-one/` to a
scratch folder, run `python moe.py new <dir> --config <dir>/room.json`,
and drive the `next` / `submit` loop with any agent command (see
SKILL.md section 2).
