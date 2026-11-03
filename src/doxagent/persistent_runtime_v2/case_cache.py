"""One fixed system-prefix breakpoint; no per-Case cache state or chunking."""

VERSION = "case-static-prefix.v1"


def split_instructions(instructions: str) -> tuple[str, str]:
    # These suffixes are emitted by Runtime itself. Keep mode/repair/IDs out of
    # the shared prefix; never split on words appearing inside business data.
    markers = (
        "\n\n## Current Capture Mode\n",
        "\n\n# Previous Attempt Correction\n",
        "\nAvailable Event IDs:",
    )
    positions = [instructions.find(marker) for marker in markers]
    split = min((position for position in positions if position >= 0), default=len(instructions))
    return instructions[:split], instructions[split:]
