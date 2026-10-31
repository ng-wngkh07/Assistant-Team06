"""Retired model-specific candidate runner; historical exclusion audit stays readable."""


def validate_defect_exclusion(def_id, accepted_ids):
    if def_id in accepted_ids:
        raise ValueError(f"Defect {def_id} was found in accepted!")


def run_candidate_audit(*args, **kwargs):
    raise RuntimeError("Retired model-specific judge protocol. A current-model reviewer requires new calibration and source approval.")


if __name__ == "__main__":
    raise SystemExit("Retired model-specific judge protocol; no candidate data were modified.")
