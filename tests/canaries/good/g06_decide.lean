-- canary: expect=proved theorem=range_sum
import Apex.Prelude

theorem range_sum : (List.range 10).foldl (· + ·) 0 = 45 := by
  decide
