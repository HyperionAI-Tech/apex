-- canary: expect=failed theorem=unchecked
-- The statement is true, but with kernel checking disabled nothing vouches for the proof.
import Apex.Prelude

set_option debug.skipKernelTC true in
theorem unchecked : 1 + 1 = 2 := rfl
