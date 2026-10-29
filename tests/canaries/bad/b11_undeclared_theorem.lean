-- canary: expect=failed theorem=not_the_approved_name
-- The file proves something, but not the theorem the audit asks for.
import Apex.Prelude

theorem something_else : 1 + 1 = 2 := rfl
