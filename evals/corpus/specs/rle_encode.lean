/-- P1: decoding the encoding gives the input back -/
theorem P1 (xs : List Int) : rle_decode (rle_encode xs) = xs := by
  sorry

/-- P2: every run has a positive count -/
theorem P2 (xs : List Int) : ∀ p ∈ rle_encode xs, 0 < p.2 := by
  sorry
