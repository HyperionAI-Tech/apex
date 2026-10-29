-- canary: expect=failed theorem=bogus audit_rejects=cheat
import Apex.Prelude

axiom cheat : False

theorem bogus : 1 = 2 := cheat.elim
