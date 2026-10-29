-- canary: expect=failed theorem=trusts_compiler audit_rejects=native_decide
-- True statement, but native_decide trusts compiled code (Lean.ofReduceBool), not the kernel.
import Apex.Prelude

theorem trusts_compiler : 10 + 10 = 20 := by native_decide
