-- canary: expect=proved theorem=length_append
import Apex.Prelude

theorem length_append (xs ys : List Int) : (xs ++ ys).length = xs.length + ys.length := by
  simp
