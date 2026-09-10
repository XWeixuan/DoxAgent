"""Fallbacks retain existing candidate text; no investment claim is generated."""

from .schema import ShellFinalizationResult, ShellSynthesisResult


def fallback(model, context):
    if model is ShellSynthesisResult:
        sets = context.get("candidate_sets", {})
        shells = []
        for role, values in sets.items():
            for item in values.get("candidates", []):
                shells.append(
                    {
                        "shell_temp_id": f"{role}:{item['candidate_id']}",
                        "core_question": item["candidate"],
                        "boundary_reasoning": item["reason"],
                        "candidate_units": [
                            {k: item[k] for k in ("candidate_ref", "candidate_id", "candidate")}
                        ],
                    }
                )
        if shells or sets:
            return model(
                provisional_shells=shells,
                warnings=["SYNTHESIS_UNAVAILABLE: candidates retained separately"],
            )
    if model is ShellFinalizationResult:
        provisional = context.get("provisional_shells")
        if isinstance(provisional, dict):
            shells = [
                {
                    "shell_id": item["shell_temp_id"],
                    "core_question": item["core_question"],
                    "boundary_rule": item["boundary_reasoning"],
                    "units": [
                        {
                            "expectation_id": unit["candidate_ref"],
                            "proposition": unit["candidate"],
                            "horizon": "UNRESOLVED",
                        }
                        for unit in item["candidate_units"]
                    ],
                }
                for item in provisional.get("provisional_shells", [])
            ]
            return model(
                shells=shells,
                warnings=[
                    "FINALIZATION_UNAVAILABLE: provisional seeds retained; horizon unresolved"
                ],
            )
    return None
