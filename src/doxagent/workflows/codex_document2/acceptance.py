"""File-first, loss-aware D2 acceptance. Quality diagnostics never gate a turn."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from doxagent.codex_runtime.recovery import ingest_model, json_value

from . import schema as s

ACCEPTANCE_VERSION = "d2-acceptance-v1"


def metadata_path(output_path):
    """Short controller paths also work in deeply nested Windows workspaces."""
    key = hashlib.sha256(output_path.encode("utf-8")).hexdigest()[:20]
    return f"artifacts/document2/acceptance/{key}.json"


class DeliveryReceipt(s.AgentModel):
    completion_path: str
    status: str | None = None


@dataclass
class Accepted:
    output: BaseModel
    source: str
    diagnostics: list[dict] = field(default_factory=list)
    raw_sha256: str | None = None

    def metadata(self):
        return dict(
            acceptance_version=ACCEPTANCE_VERSION,
            source=self.source,
            raw_sha256=self.raw_sha256,
            diagnostics=self.diagnostics,
        )


def diagnostic(items, code, path="", **details):
    item = dict(code=code, path=path, **details)
    if item not in items:
        items.append(item)


def merge_discovery_records(previous, current):
    """Keep first discovery provenance and latest authored text/closure, in stable order."""
    additions, resolutions = {}, {}
    for source in (previous, current):
        for item in source.get("late_additions", []):
            key = (item["unit"], item["name"])
            first = additions.get(key)
            additions[key] = {**item}
            if first:
                additions[key]["discovered_during"] = first["discovered_during"]
        for item in source.get("open_discovery_resolution", []):
            resolutions[(item["unit"], item["candidate"])] = {**item}
    return dict(
        late_additions=list(additions.values()),
        open_discovery_resolution=list(resolutions.values()),
    )


def history(context):
    return dict(
        late_additions=context.get("open_discovery_late_additions", []),
        open_discovery_resolution=context.get("open_discovery_resolution", []),
    )


def _dedup(items, key, diagnostics, path):
    result = {}
    for item in items:
        identity = key(item)
        if identity in result and result[identity] != item:
            diagnostic(
                diagnostics,
                "conflicting_duplicate",
                path,
                identity=str(identity),
                alternative=result[identity],
            )
        result[identity] = item
    return list(result.values())


def normalize(output, context, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else []
    raw = output.model_dump(mode="json")
    if isinstance(output, s.CandidateDiscoveryResultV21):
        raw["candidates"] = _dedup(
            raw["candidates"], lambda x: x["name"], diagnostics, "candidates"
        )
    elif isinstance(output, s.ShellSynthesisResultV21):
        raw["provisional_shells"] = _dedup(
            raw["provisional_shells"],
            lambda x: x["shell_temp_id"],
            diagnostics,
            "provisional_shells",
        )
        expected = {
            c["candidate_ref"]: c
            for values in context.get("candidate_sets", {}).values()
            for c in values.get("candidates", [])
        }
        seen = set()
        for sh in raw["provisional_shells"]:
            kept = []
            for c in sh["candidate_units"]:
                ref = c["candidate_ref"]
                if ref not in expected or ref in seen:
                    diagnostic(
                        diagnostics, "candidate_disposition_isolated", "candidate_units", original=c
                    )
                else:
                    kept.append(c)
                    seen.add(ref)
            sh["candidate_units"] = kept
        kept = []
        for c in raw["unassigned_candidates"]:
            ref = c["candidate_ref"]
            if ref in expected and ref not in seen:
                kept.append(c)
                seen.add(ref)
            else:
                diagnostic(
                    diagnostics,
                    "candidate_disposition_isolated",
                    "unassigned_candidates",
                    original=c,
                )
        for ref, c in expected.items():
            if ref not in seen:
                kept.append(
                    {**c, "reason": "Not disposed by this turn; retained by orchestration."}
                )
                diagnostic(diagnostics, "candidate_unassigned", ref)
        raw["unassigned_candidates"] = kept
        raw["provisional_shells"] = _dedup(
            raw["provisional_shells"],
            lambda x: x["shell_temp_id"],
            diagnostics,
            "provisional_shells",
        )
    elif isinstance(output, s.ShellFinalizationResultV21):
        raw["shells"] = _dedup(raw["shells"], lambda x: x["name"], diagnostics, "shells")
        for sh in raw["shells"]:
            sh["units"] = _dedup(sh["units"], lambda x: x["name"], diagnostics, "units")
    elif isinstance(output, s.OpenDiscoveryScanV21):
        seed = context["canonical_shell"]
        if raw["shell"] != seed["name"]:
            raise ValueError("scan belongs to another Shell")
        units = {
            u["name"]: u
            for u in _dedup(raw["units"], lambda x: x["name"], diagnostics, "scan.units")
        }
        for name in set(units) - {u["name"] for u in seed["units"]}:
            diagnostic(diagnostics, "unknown_scan_unit", name, original=units.pop(name))
        for unit in seed["units"]:
            if unit["name"] not in units:
                units[unit["name"]] = dict(name=unit["name"], candidates=[])
                diagnostic(diagnostics, "scan_unit_not_researched", unit["name"])
        raw["units"] = list(units.values())
        for unit in raw["units"]:
            unit["candidates"] = _dedup(
                unit["candidates"], lambda x: x["name"], diagnostics, f"scan.{unit['name']}"
            )
    elif isinstance(output, s.OpenDiscoverySelectionV21):
        scan = context["open_discovery_scan"]
        if raw["shell"] != scan["shell"]:
            raise ValueError("selection belongs to another Shell")
        expected = {(u["name"], c["name"]) for u in scan["units"] for c in u["candidates"]}
        by_key = {
            (c["unit"], c["candidate"]): c
            for c in _dedup(
                raw["selections"], lambda x: (x["unit"], x["candidate"]), diagnostics, "selection"
            )
        }
        for key in list(by_key):
            if key not in expected:
                diagnostic(diagnostics, "unknown_selection", str(key), original=by_key.pop(key))
        invalid = set()
        for key, item in by_key.items():
            if item["decision"] != "MERGE":
                if item["merge_into"] is not None:
                    diagnostic(
                        diagnostics,
                        "non_merge_target_removed",
                        str(key),
                        original=item["merge_into"],
                    )
                    item["merge_into"] = None
                continue
            seen, current = {key}, item
            while current["decision"] == "MERGE":
                target = (current["unit"], current["merge_into"])
                if target not in by_key or target in seen:
                    invalid.add(key)
                    break
                seen.add(target)
                current = by_key[target]
            if current["decision"] == "DROP":
                invalid.add(key)
        for key in invalid:
            diagnostic(diagnostics, "selection_pending", str(key), original=by_key.pop(key))
        for key in sorted(expected - by_key.keys()):
            diagnostic(diagnostics, "selection_pending", str(key), unit=key[0], candidate=key[1])
        raw["selections"] = list(by_key.values())
    elif isinstance(output, s.ShellResearchTurnResultV21):
        shell = raw["canonical_shell"]
        prior_shell = context["canonical_shell"]
        if shell["name"] != prior_shell["name"]:
            raise ValueError("research belongs to another Shell")
        if not shell["units"] and prior_shell["units"]:
            diagnostic(diagnostics, "empty_shell_reused_upstream", "canonical_shell")
            shell = raw["canonical_shell"] = copy.deepcopy(prior_shell)
        shell["units"] = _dedup(shell["units"], lambda x: x["name"], diagnostics, "units")
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
        prior_units = {u["name"]: u for u in prior_shell["units"]}
        for unit in shell["units"]:
            for owner, name in (
                (unit["state"], "parameters"),
                (unit["state"], "values"),
                (unit, "expectation_baseline"),
                (unit, "realization_factors"),
                (unit, "potential_gaps"),
            ):
                owner[name] = _dedup(
                    owner[name], lambda x: x["name"], diagnostics, f"{unit['name']}.{name}"
                )
            parameters = {p["name"]: p for p in unit["state"]["parameters"]}
            prior = prior_units.get(unit["name"], {}).get("state", {}).get("values", [])
            prior_values = {v["name"]: v for v in prior}
            valid = []
            for value in unit["state"]["values"]:
                parameter = parameters.get(value["parameter"])
                try:
                    if parameter is None:
                        raise ValueError("unknown parameter")
                    parsed = s.StateValueV21.model_validate(value)
                    t = types[parameter["value_type"]]
                    if not isinstance(parsed.value, t) or (
                        parsed.previous_value is not None
                        and not isinstance(parsed.previous_value, t)
                    ):
                        raise ValueError("incompatible value type")
                except ValueError as exc:
                    diagnostic(
                        diagnostics,
                        "value_isolated",
                        f"{unit['name']}.{value['name']}",
                        reason=str(exc),
                        original=value,
                    )
                    previous = prior_values.get(value["name"])
                    if previous and previous != value:
                        p = parameters.get(previous["parameter"])
                        if p:
                            pv = s.StateValueV21.model_validate(previous)
                            t = types[p["value_type"]]
                            if isinstance(pv.value, t) and (
                                pv.previous_value is None or isinstance(pv.previous_value, t)
                            ):
                                valid.append(previous)
                    continue
                valid.append(value)
            unit["state"]["values"] = valid
            for gap in unit["potential_gaps"]:
                gap["possibility_space"] = _dedup(
                    gap["possibility_space"],
                    lambda x: x["name"],
                    diagnostics,
                    f"{unit['name']}.{gap['name']}",
                )
        for name, key in (("late_additions", "name"), ("open_discovery_resolution", "candidate")):
            raw[name] = _dedup(raw[name], lambda x, key=key: (x["unit"], x[key]), diagnostics, name)
        old = history(context)
        old_keys = {(c["unit"], c["name"]): c for c in old["late_additions"]}
        for c in raw["late_additions"]:
            key = (c["unit"], c["name"])
            if key in old_keys:
                c["discovered_during"] = old_keys[key]["discovered_during"]
            elif c["discovered_during"] != context.get("turn"):
                diagnostic(
                    diagnostics,
                    "late_addition_provenance",
                    str(key),
                    authored=c["discovered_during"],
                    received=context.get("turn"),
                )
            if c["unit"] not in set(prior_units) | {u["name"] for u in shell["units"]}:
                diagnostic(diagnostics, "late_addition_unit_unlocated", str(key))
        raw.update(merge_discovery_records(old, raw))
        if context.get("turn") == "FINALIZATION":
            required = {
                (c["unit"], c["candidate"])
                for c in context.get("open_discovery_selection", {}).get("selections", [])
                if c["decision"] == "DEEPEN"
            }
            required.update((c["unit"], c["name"]) for c in raw["late_additions"])
            resolved = {
                (r["unit"], r["candidate"])
                for r in raw["open_discovery_resolution"]
                if r["resolution"].strip()
            }
            for key in sorted(required - resolved):
                diagnostic(
                    diagnostics, "resolution_unresolved", str(key), unit=key[0], candidate=key[1]
                )
        destinations = {u["name"] for u in shell["units"]}
        destinations.update(
            x["name"]
            for u in shell["units"]
            for x in u["realization_factors"] + u["potential_gaps"]
        )
        destinations.update(
            u["name"]
            for sh in context.get("o0_finalization", {}).get("shells", [])
            for u in sh["units"]
        )
        for r in raw["open_discovery_resolution"]:
            if not r["resolution"].strip() or (
                r["destination"] is not None and r["destination"] not in destinations
            ):
                diagnostic(
                    diagnostics, "resolution_unlocated", f"{r['unit']}.{r['candidate']}", original=r
                )
    return type(output).model_validate(raw)


def _fallback(model, context):
    from .recovery import fallback

    if model is s.ShellResearchTurnResultV21:
        return model(canonical_shell=context["canonical_shell"], **history(context))
    if model is s.ExpectationShell:
        return model.model_validate(context["canonical_shell"])
    if model is s.OpenDiscoverySelectionV21:
        return model(shell=context["open_discovery_scan"]["shell"])
    if model in (s.CandidateDiscoveryResult, s.CandidateDiscoveryResultV21):
        return model(warnings=["CANDIDATE_UNAVAILABLE: domain not researched"])
    if model in (s.DomainReviewResult, s.DomainReviewResultV21):
        role = str(context.get("reviewer_role", context.get("domain", "C1"))).upper()
        return model(
            reviewer_role=role if role in {"C1", "C3", "C5"} else "C1",
            overall_assessment="REVIEW_UNAVAILABLE",
            warnings=["Review unavailable; provisional retained"],
        )
    return fallback(model, context)


def _salvage(model, raw, context, diagnostics):
    """Prune only the nearest invalid list item; use upstream required fields when known."""
    raw = copy.deepcopy(raw)
    if not isinstance(raw, dict):
        return None
    if model is s.ShellResearchTurnResultV21:
        shell = raw.get("canonical_shell")
        if isinstance(shell, dict):
            previous = context["canonical_shell"]
            for key in ("name", "scope", "boundary", "ref"):
                if key not in shell:
                    shell[key] = copy.deepcopy(previous[key])
            units = {u["name"]: u for u in previous["units"]}
            for unit in shell.get("units", []):
                if isinstance(unit, dict) and unit.get("name") in units:
                    for key in ("scope", "horizon", "ref"):
                        if key not in unit:
                            unit[key] = copy.deepcopy(units[unit["name"]][key])
    # Every pass removes an actual authored invalid item, so progress is bounded by input.
    while True:
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            removed = False
            for error in exc.errors():
                value, nearest = raw, None
                for part in error["loc"]:
                    if isinstance(value, list) and isinstance(part, int) and part < len(value):
                        nearest = (value, part)
                        value = value[part]
                    elif isinstance(value, dict) and part in value:
                        value = value[part]
                    else:
                        break
                if nearest:
                    array, i = nearest
                    original = array.pop(i)
                    diagnostic(
                        diagnostics,
                        "invalid_item_isolated",
                        str(error["loc"]),
                        original=original,
                    )
                    location = error["loc"]
                    if (
                        model is s.ShellResearchTurnResultV21
                        and len(location) >= 5
                        and location[:2] == ("canonical_shell", "units")
                        and location[3:5] == ("state", "values")
                        and isinstance(original, dict)
                    ):
                        unit = raw["canonical_shell"]["units"][location[2]]
                        previous = next(
                            (
                                v
                                for u in context["canonical_shell"]["units"]
                                if u["name"] == unit["name"]
                                for v in u["state"]["values"]
                                if v["name"] == original.get("name")
                            ),
                            None,
                        )
                        if previous is not None and previous != original:
                            s.StateValueV21.model_validate(previous)
                            array.insert(i, copy.deepcopy(previous))
                            diagnostic(diagnostics, "value_reused_upstream", str(location))
                    removed = True
                    break
            if not removed:
                return None


def accept(model, context, *, file_text=None, sdk_reply=None):
    parsed_sources, errors = [], []
    for source, text in (("file", file_text), ("sdk_reply", sdk_reply)):
        if text is None:
            continue
        try:
            raw = json_value(text)
            if not isinstance(raw, dict) or ("completion_path" in raw and len(raw) <= 2):
                continue
            parsed_sources.append((source, text, raw))
            parsed = ingest_model(model, raw)
            ds = []
            normalized = normalize(parsed, context, ds)
        except (ValueError, TypeError, KeyError) as exc:
            diagnostic(errors, "source_unusable", source, reason=str(exc)[:1000])
            continue
        if source == "file" and sdk_reply:
            try:
                reply = json_value(sdk_reply)
                if isinstance(reply, dict) and "completion_path" not in reply and reply != raw:
                    diagnostic(ds, "sdk_reply_mismatch")
            except (ValueError, TypeError):
                diagnostic(ds, "sdk_reply_unusable")
        return Accepted(
            normalized, source, ds + errors, hashlib.sha256(text.encode("utf-8")).hexdigest()
        )
    for source, text, raw in parsed_sources:
        ds = list(errors)
        parsed = _salvage(model, raw, context, ds)
        if parsed is not None:
            try:
                parsed = normalize(parsed, context, ds)
            except (ValueError, KeyError):
                continue
            diagnostic(ds, "partial_result_salvaged", source)
            return Accepted(parsed, source, ds, hashlib.sha256(text.encode("utf-8")).hexdigest())
    fallback = _fallback(model, context)
    if fallback is None:
        raise ValueError(f"No consumable {model.__name__} or reliable upstream")
    diagnostic(errors, "upstream_fallback", model.__name__)
    return Accepted(normalize(fallback, context, errors), "upstream", errors)


def accept_discovery(checkpoint, context, *, file_text=None, sdk_reply=None, selection_bound=True):
    """Only real checkpoint identity matters; an incorrect reply hash is repairable."""
    ds = []

    def selection(text):
        if text is None:
            return None
        try:
            raw = json_value(text)
            if "selection" in raw:
                claimed = raw.get("scan_sha256", raw.get("checkpoint", {}).get("scan_sha256"))
                if claimed != checkpoint.scan_sha256:
                    diagnostic(
                        ds,
                        "completion_binding_repaired",
                        authored=claimed,
                        actual=checkpoint.scan_sha256,
                    )
                return json.dumps(raw["selection"], ensure_ascii=False)
        except (ValueError, TypeError, KeyError):
            pass
        return text

    ctx = {**context, "open_discovery_scan": checkpoint.scan.model_dump(mode="json")}
    if selection_bound:
        accepted = accept(
            s.OpenDiscoverySelectionV21,
            ctx,
            file_text=selection(file_text),
            sdk_reply=selection(sdk_reply),
        )
    else:
        diagnostic(ds, "selection_not_bound_at_freeze", original=file_text or sdk_reply)
        accepted = accept(s.OpenDiscoverySelectionV21, ctx)
    accepted.output = s.OpenDiscoveryResultV21(checkpoint=checkpoint, selection=accepted.output)
    accepted.diagnostics = ds + accepted.diagnostics
    return accepted
