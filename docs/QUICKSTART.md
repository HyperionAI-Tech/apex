# Quickstart

Ten minutes from install to a machine-checked proof, then a planted bug caught with a
counterexample. Every output below is from a real run, trimmed.

## 1. Install

```sh
uv build && uv tool install dist/apex_verify-*.whl   # not on PyPI yet
apex init      # once per machine: Lean toolchain + prebuilt Mathlib cache, about 7 GB
apex doctor    # ok everywhere except optional extras (warn)
```

`apex init` installs into `~/.apex` (set `APEX_HOME` to move it), shared by every project.
Refutation works without it. Only the Lean verbs need it, and they exit 3 with a pointer to
`apex init` until it has run.

## 2. Write down what the function should do

Apex proves what you already said the function should do. Any one of these is enough:

- a docstring with `pre:` / `post:` lines and doctests (used below);
- tests in `test_<module>.py` next to the module or in `tests/`: literal asserts become
  examples, and `@given` properties become formal properties;
- an English spec in `<module>.spec.md`, one requirement per list item, with an optional
  formal reading in backticks: `` - The result lies in [lo, hi]. `lo <= r <= hi` ``;
- nothing but the code: leading `assert`s become preconditions, and a model can propose
  properties, which count only after you approve them.

```python
# shapes.py
def clamp(x: int, lo: int, hi: int) -> int:
    """Limit x to the range [lo, hi].

    pre: lo <= hi
    post: lo <= __return__ <= hi
    post: lo <= x <= hi -> __return__ == x

    >>> clamp(5, 0, 3)
    3
    """
    return max(lo, min(x, hi))
```

## 3. Check it

```sh
apex check shapes.py
```

```
  shapes.py::clamp tactics: closed P1, P2, E1; open none
                            Apex 0.1.0.dev0 · exit 2
│ shapes.clamp │ P1 │ proven_unreviewed │ statement pending: run `apex review shapes.py::clamp --approve P1` in a terminal │
│ shapes.clamp │ P2 │ proven_unreviewed │ ...
│ shapes.clamp │ E1 │ proven_unreviewed │ ...
  fidelity shapes.clamp: Lean model agrees with Python on 200 generated inputs
```

Apex translated `clamp` into a Lean model and drafted one Lean theorem per property: P1 and P2
from the `post:` lines, E1 from the doctest. Lean proved all three, and the compiled model agreed
with the real Python on 200 generated inputs. The verdict is still `proven_unreviewed` (exit 2),
because nobody has checked that the Lean statements say what you meant.

## 4. Approve the statements

```sh
apex review shapes.py::clamp                    # each statement in English and in Lean
apex review shapes.py::clamp --approve P1,P2,E1
apex check shapes.py                            # proven, exit 0
```

Read the Lean before approving it. A proof is only as good as its statement. Approval needs an
interactive terminal, so a coding agent's shell cannot approve on your behalf. Approvals go to
`.apex/reviews.json`; commit that file and CI reproduces `proven` without a person. Any change
to the function or to a statement voids its approval.

## 5. Break it

Change `min(x, hi)` to `min(x, hi - 1)` and check again:

```
                            Apex 0.1.0.dev0 · exit 1
│ shapes.clamp │ P1 │ proven_unreviewed │ ...
│ shapes.clamp │ P2 │ refuted           │ input (x=1, lo=0, hi=1): lo <= x <= hi -> r == x; returns 0 │
│ shapes.clamp │ E1 │ refuted           │ input (x=5, lo=0, hi=3): 3; returns 2 │
```

P1 still holds: the buggy result does stay in range. P2 and the doctest do not. Each
counterexample was replayed on the real Python before it was reported. `apex suggest` turns each
one into a regression test:

```python
def test_clamp_P2_apex_counterexample() -> None:
    # Found by hypothesis; property P2: lo <= x <= hi -> r == x
    x = 1
    lo = 0
    hi = 1
    r = clamp(x, lo, hi)
    assert (not (lo <= x <= hi)) or (r == x)
```

`apex explain` says in plain English what each verdict means and what to do next.

## 6. Let a model help (optional)

Tactics close simple goals. For harder ones, give `apex check` a model. It can be an API key or
a coding agent you already have:

```sh
apex check shapes.py --provider claude-code     # or codex, anthropic, openai
```

The model proposes proofs, and Lean checks every one with the same rules as a hand-written
proof. Model output is only ever a proposal. It never decides a verdict, and it cannot approve a
statement.

## 7. Use it from a coding agent

```sh
apex skill install --with-mcp    # the apex-verify skill + MCP server, for every harness
```

Then ask your agent, in your own words, to formally verify a function. The skill drives Apex,
shows Apex's table verbatim, and asks you to approve statements yourself.

## 8. Put it in CI

```yaml
- run: apex check src/ --sarif apex.sarif   # exit 1 fails the job; gaps (exit 2) do not
- uses: github/codeql-action/upload-sarif@v3
  if: always()
  with: { sarif_file: apex.sarif }
```

## Next

- [What Apex cannot prove](LIMITS.md): read it before trusting a verdict.
- `apex check --help`, `apex explain`, and the README's exit-code table.
