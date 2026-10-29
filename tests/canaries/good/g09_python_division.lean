-- canary: expect=proved theorem=python_floor_semantics
-- Python: -7 // 2 == -4 and -7 % 2 == 1 (floor semantics, unlike C truncation).
import Apex.Prelude

theorem python_floor_semantics : Apex.pyFloorDiv (-7) 2 = -4 ∧ Apex.pyMod (-7) 2 = 1 := by
  decide
