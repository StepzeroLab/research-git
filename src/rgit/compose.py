from __future__ import annotations
from collections import defaultdict

from .astmap import read_symbol_source
from .store.store import Store


def compose(store: Store, feature_ids: list[str]) -> dict:
    """Assemble a regeneration brief for the host agent."""
    features = []
    touch: dict[tuple[str, str], list[str]] = defaultdict(list)
    for fid in feature_ids:
        cap = store.get_feature(fid)
        current = {}
        for s in cap.code_slices:
            if s.symbol:
                current[s.symbol] = read_symbol_source(store.root, s.file, s.symbol) or ""
                touch[(s.file, s.symbol)].append(cap.name)
        features.append({
            "id": fid, "name": cap.name, "intent": cap.intent,
            "knobs": cap.knobs, "data_assumptions": cap.data_assumptions,
            "resurrection_guide": cap.resurrection_guide,
            "code_slices": [s.__dict__ for s in cap.code_slices],
            "current_source": current,
        })
    conflicts = [{"file": f, "symbol": s, "features": names}
                 for (f, s), names in touch.items() if len(names) > 1]

    by_id = {fid: store.get_feature(fid) for fid in feature_ids}
    name_to_cap = {cap.name: cap for cap in by_id.values()}
    merge_context = []
    for (f, s), names in touch.items():
        if len(names) <= 1:
            continue
        contributors = []
        for nm in names:
            cap = name_to_cap[nm]
            slice_code = next((sl.code for sl in cap.code_slices
                               if sl.file == f and sl.symbol == s), "")
            contributors.append({"capsule": nm, "clean_slice": slice_code,
                                 "intent": cap.intent, "knobs": cap.knobs})
        merge_context.append({
            "file": f, "symbol": s,
            "current_source": read_symbol_source(store.root, f, s) or "",
            "contributors": contributors})

    return {"features": features, "conflicts": conflicts,
            "merge_context": merge_context}
