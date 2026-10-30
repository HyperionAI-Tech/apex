/-- P1: the result is one of the inputs -/
theorem P1 (a b c : Int) : median3 a b c = a ∨ median3 a b c = b ∨ median3 a b c = c := by
  sorry

/-- P2: the result lies between the minimum and the maximum -/
theorem P2 (a b c : Int) :
    min a (min b c) ≤ median3 a b c ∧ median3 a b c ≤ max a (max b c) := by
  sorry

/-- P3: the order of the first two arguments does not matter -/
theorem P3 (a b c : Int) : median3 a b c = median3 b a c := by
  sorry
