-- canary: expect=proved theorem=reverse_reverse
import Apex.Prelude

theorem reverse_reverse (xs : List Int) : xs.reverse.reverse = xs := by
  simp
