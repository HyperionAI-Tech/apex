/-- P1: when lo ≤ hi, the result lies in [lo, hi] -/
theorem P1 (x lo hi : Int) (h : lo ≤ hi) : lo ≤ clamp x lo hi ∧ clamp x lo hi ≤ hi := by
  sorry

/-- P2: values already in range are returned unchanged -/
theorem P2 (x lo hi : Int) (h1 : lo ≤ x) (h2 : x ≤ hi) : clamp x lo hi = x := by
  sorry
