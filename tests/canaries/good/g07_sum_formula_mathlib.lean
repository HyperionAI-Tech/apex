-- canary: expect=proved theorem=sumTo_formula timeout=600
-- Exercises the Mathlib path (ring), not just core tactics.
import Mathlib.Tactic.Ring

def sumTo : Nat → Nat
  | 0 => 0
  | n + 1 => (n + 1) + sumTo n

theorem sumTo_formula (n : Nat) : 2 * sumTo n = n * (n + 1) := by
  induction n with
  | zero => rfl
  | succ k ih =>
    unfold sumTo
    rw [Nat.mul_add, ih]
    ring
