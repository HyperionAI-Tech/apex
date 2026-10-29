/-- P1: same length as the input -/
theorem P1 (xs : List Int) : (double_all xs).length = xs.length := by
  sorry

/-- P2: the sum doubles -/
theorem P2 (xs : List Int) : Apex.pySum (double_all xs) = 2 * Apex.pySum xs := by
  sorry
