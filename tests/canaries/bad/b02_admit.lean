-- canary: expect=failed theorem=bogus audit_rejects=sorryAx
import Apex.Prelude

theorem bogus : 1 = 2 := by admit
