/-- P1: same length -/
theorem P1 (xs : List Int) (lo hi : Int) : (clamp_all xs lo hi).length = xs.length := by
  sorry

/-- P2: every element lies in [lo, hi] -/
theorem P2 (xs : List Int) (lo hi : Int) (h : lo ≤ hi) :
    ∀ v ∈ clamp_all xs lo hi, lo ≤ v ∧ v ≤ hi := by
  sorry
