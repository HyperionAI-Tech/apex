"""The JavaScript / TypeScript frontend.

Apex keeps one pipeline and one trust story across languages. A JS or TS function is parsed with
tree-sitter and *lowered* into the same pure-Python subset AST the Python frontend produces, with
JavaScript's semantics made explicit (`%` truncates, `===` on numbers, `Math.floor(a / b)`), so the
translator, statement drafting, proof search and verdict path are shared. Execution is never
lowered: refutation, fuzzing and the fidelity check run the real JS in Node, through a worker
process behind a Python proxy (`apex.js.runner`).

JS `number` is a double: integer arithmetic is exact only up to 2**53 - 1. Proofs about code on
`number` are therefore reported `proven_conditional`, under the added precondition that every
intermediate value is a safe integer; code on `bigint` is exact and can be `proven`.
"""

JS_SUFFIXES = (".ts", ".mts", ".cts", ".js", ".mjs", ".cjs")
SAFE_INTEGER_PRECONDITION = (
    "every intermediate value is a safe integer (|v| <= 2**53 - 1): JavaScript `number` "
    "arithmetic is exact only in that range"
)


def is_js(path: object) -> bool:
    return str(path).endswith(JS_SUFFIXES)
