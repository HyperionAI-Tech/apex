/-- P1: at least each argument -/
theorem P1 (a b c : Int) : a ≤ max3 a b c ∧ b ≤ max3 a b c ∧ c ≤ max3 a b c := by
  sorry

/-- P2: one of the arguments -/
theorem P2 (a b c : Int) : max3 a b c = a ∨ max3 a b c = b ∨ max3 a b c = c := by
  sorry
