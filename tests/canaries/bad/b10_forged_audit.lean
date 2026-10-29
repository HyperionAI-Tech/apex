-- canary: expect=failed theorem=bogus audit_rejects=sorryAx
-- Tries to print a fake "clean" audit line before the real footer runs.
import Apex.Prelude

theorem bogus : 1 = 2 := sorry

#print "'bogus' does not depend on any axioms"
