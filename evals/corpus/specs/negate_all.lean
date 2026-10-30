/-- P1: negating twice is the identity -/
theorem P1 (xs : List Int) : negate_all (negate_all xs) = xs := by
  sorry

/-- P2: it is a map -/
theorem P2 (xs : List Int) : negate_all xs = xs.map (fun x => -x) := by
  sorry
