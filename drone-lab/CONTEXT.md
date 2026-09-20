# Drone Lab

A playground for testing language-driven drone decisions in simulation before connecting a physical device.

## Language

**Command**: A user's requested drone action expressed as text or reviewed voice transcription.
_Avoid_: Motor instruction, control packet

**Decision**: A classified action, qualitative step size and clarification flag. It is a proposal that must pass the current flight-state checks.
_Avoid_: Flight authorization

**Observation**: A timestamped simulator state or camera interpretation. A camera interpretation can be uncertain and does not itself cause flight.
_Avoid_: Ground truth, safe-flight verdict

**Simulation unit**: A distance unit in Drone Commander's virtual world, without a calibrated PL-515 meter equivalent.
_Avoid_: Meter

**Hold**: Cancelling queued movement while retaining the simulated pose. Physical hold requires its own tested implementation.
_Avoid_: Land, emergency stop

**Land**: A controlled descent to a surface in simulation. A physical driver's similarly named command may behave differently.
_Avoid_: Motor cutoff

**Hardware adapter**: The boundary that translates an approved operation into a particular device's verified capabilities and protocol.
_Avoid_: Simulator replacement, generic drone SDK
