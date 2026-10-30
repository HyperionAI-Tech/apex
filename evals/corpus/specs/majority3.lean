/-- P1: two equal votes decide -/
theorem P1 (a c : Bool) : majority3 a a c = a := by
  sorry

/-- P2: rotating the votes does not change the result -/
theorem P2 (a b c : Bool) : majority3 a b c = majority3 b c a := by
  sorry

/-- P3: swapping the first two votes does not change the result -/
theorem P3 (a b c : Bool) : majority3 a b c = majority3 b a c := by
  sorry
