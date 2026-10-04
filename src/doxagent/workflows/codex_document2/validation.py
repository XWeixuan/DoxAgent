"""Context-bound structural checks; research judgments remain agent-owned."""

from . import schema as s


def _unique(values, path):
    if len(values) != len(set(values)):
        raise ValueError(f"{path}: duplicate names or identities")


def validate_output(output, context):
    if isinstance(output, s.OpenDiscoveryResultV21):
        from .discovery_checkpoint import validate_checkpoint

        validate_checkpoint(
            output.checkpoint,
            context,
            context.get("workspace_run_id", output.checkpoint.workspace_run_id),
        )
        validate_output(
            output.selection,
            {**context, "open_discovery_scan": output.checkpoint.scan.model_dump(mode="json")},
        )
    elif isinstance(output, s.CandidateDiscoveryResultV21):
        _unique([c.name for c in output.candidates], "candidates.name")
    elif isinstance(output, s.ShellSynthesisResultV21):
        expected = {
            c["candidate_ref"]
            for group in context.get("candidate_sets", {}).values()
            for c in group["candidates"]
        }
        actual = [c.candidate_ref for sh in output.provisional_shells for c in sh.candidate_units]
        actual.extend(c.candidate_ref for c in output.unassigned_candidates)
        _unique(actual, "candidate_ref")
        if set(actual) != expected:
            raise ValueError("candidate_ref: disposition must cover exactly the input candidates")
    elif isinstance(output, s.ShellFinalizationResultV21):
        _unique([sh.name for sh in output.shells], "shells.name")
        for sh in output.shells:
            _unique([u.name for u in sh.units], f"{sh.name}.units.name")
    elif isinstance(output, s.OpenDiscoveryScanV21):
        shell = context["canonical_shell"]
        if output.shell != shell["name"]:
            raise ValueError("scan.shell: mismatched shell")
        names = [u.name for u in output.units]
        _unique(names, "scan.units")
        if set(names) != {u["name"] for u in shell["units"]}:
            raise ValueError("scan.units: cover each canonical input unit exactly once")
        for unit in output.units:
            _unique([c.name for c in unit.candidates], f"scan.{unit.name}.candidates")
    elif isinstance(output, s.OpenDiscoverySelectionV21):
        scan = s.OpenDiscoveryScanV21.model_validate(context["open_discovery_scan"])
        if output.shell != scan.shell:
            raise ValueError("selection.shell: mismatched shell")
        expected = {(u.name, c.name) for u in scan.units for c in u.candidates}
        by_key = {(c.unit, c.candidate): c for c in output.selections}
        if len(by_key) != len(output.selections) or set(by_key) != expected:
            raise ValueError("selection: cover each frozen scan candidate exactly once")
        for key, selection in by_key.items():
            if selection.decision != "MERGE":
                if selection.merge_into is not None:
                    raise ValueError(f"selection.{key}: non-MERGE target must be null")
                continue
            seen = {key}
            current = selection
            while current.decision == "MERGE":
                target = (current.unit, current.merge_into)
                if target not in by_key or target in seen:
                    raise ValueError(f"selection.{key}: invalid target or MERGE cycle")
                seen.add(target)
                current = by_key[target]
    elif isinstance(output, s.ShellResearchTurnResultV21):
        shell = output.canonical_shell
        _unique([u.name for u in shell.units], "canonical_shell.units")
        types = dict(
            zip(
                s.ParameterValueType,
                (
                    s.NumberValue,
                    s.RangeValue,
                    s.TimeValue,
                    s.StageValue,
                    s.DirectionValue,
                    s.EvidenceValue,
                ),
                strict=True,
            )
        )
        for unit in shell.units:
            for field, items in (
                ("parameters", unit.state.parameters),
                ("values", unit.state.values),
                ("baseline", unit.expectation_baseline),
                ("factors", unit.realization_factors),
                ("gaps", unit.potential_gaps),
            ):
                _unique([item.name for item in items], f"{unit.name}.{field}")
            parameters = {p.name: p for p in unit.state.parameters}
            for value in unit.state.values:
                if value.parameter not in parameters:
                    raise ValueError(f"{unit.name}.{value.name}.parameter: unknown parameter")
                expected_type = types[parameters[value.parameter].value_type]
                if not isinstance(value.value, expected_type) or (
                    value.previous_value is not None
                    and not isinstance(value.previous_value, expected_type)
                ):
                    raise ValueError(f"{unit.name}.{value.name}.value: incompatible value type")
            for gap in unit.potential_gaps:
                _unique(
                    [p.name for p in gap.possibility_space], f"{unit.name}.{gap.name}.possibilities"
                )
        scan = s.OpenDiscoveryScanV21.model_validate(context["open_discovery_scan"])
        selection = s.OpenDiscoverySelectionV21.model_validate(context["open_discovery_selection"])
        known_units = (
            {u.name for u in scan.units}
            | {u.name for u in shell.units}
            | {u["name"] for u in context["canonical_shell"]["units"]}
        )
        _unique([(c.unit, c.name) for c in output.late_additions], "late_additions")
        for candidate in output.late_additions:
            if candidate.unit not in known_units or candidate.discovered_during != context["turn"]:
                raise ValueError("late_additions: unknown unit or incorrect discovered_during")
        if context["turn"] == "FINALIZATION":
            expected = {
                (c.unit, c.candidate) for c in selection.selections if c.decision == "DEEPEN"
            }
            expected.update(
                (c["unit"], c["name"]) for c in context.get("open_discovery_late_additions", [])
            )
            expected.update((c.unit, c.name) for c in output.late_additions)
            resolutions = {(r.unit, r.candidate): r for r in output.open_discovery_resolution}
            _unique([(r.unit, r.candidate) for r in output.open_discovery_resolution], "resolution")
            if not expected.issubset(resolutions):
                raise ValueError(
                    f"resolution: missing closure for {sorted(expected - resolutions.keys())}"
                )
            destinations = {u.name for u in shell.units}
            destinations.update(
                x.name for u in shell.units for x in (*u.realization_factors, *u.potential_gaps)
            )
            destinations.update(
                u["name"]
                for sh in context["o0_finalization"]["shells"]
                if sh["name"] != scan.shell
                for u in sh["units"]
            )
            for r in resolutions.values():
                if not r.resolution.strip() or (
                    r.destination is not None and r.destination not in destinations
                ):
                    raise ValueError(
                        f"resolution.{r.unit}.{r.candidate}: "
                        "empty resolution or unknown destination"
                    )
